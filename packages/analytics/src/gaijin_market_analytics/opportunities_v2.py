from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Literal

from .historical_trades import (
    HistoricalTradeBucketEvidence,
    HistoricalTradeEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .absorption_exit import (
    AbsorptionWithholdPolicy,
    SupplyAbsorptionExitSnapshot,
    resolve_absorption_withholding,
)
from .manual_context import (
    ManualMarketContextObservation,
    context_comparability,
    resolve_manual_market_context,
    weighted_decimal_median,
)
from .reissue_exposure import ReissueExposureFeatures
from .supply_channels import (
    ChannelCoveragePolicy,
    SupplyChannelCoverage,
    resolve_channel_coverage_withholding,
)
from .supply_cycles import SupplyCycleFeatures

LiquiditySource = Literal["reviewed_screenshot_quantity", "snapshot_counts", "unavailable"]


class OpportunityV2ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CurrentMarketEvidence:
    item_id: int
    observed_at: datetime
    best_ask: Decimal | None
    best_bid: Decimal | None
    ask_count: int | None = None
    bid_count: int | None = None
    observed_ask_quantity: int | None = None
    observed_bid_quantity: int | None = None

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise OpportunityV2ContractError("item_id must be positive.")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        for name in ("best_ask", "best_bid"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0")
            ):
                raise OpportunityV2ContractError(f"{name} must be a positive finite Decimal.")
        for name in (
            "ask_count",
            "bid_count",
            "observed_ask_quantity",
            "observed_bid_quantity",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise OpportunityV2ContractError(f"{name} must be non-negative.")


@dataclass(frozen=True, slots=True)
class OpportunityV2Config:
    maximum_current_market_age: timedelta = timedelta(days=7)
    maximum_historical_trade_age: timedelta = timedelta(days=7)
    minimum_historical_bucket_count: int = 5
    minimum_effective_day_count: int = 3
    minimum_observed_span_ratio: Decimal = Decimal("0.50")
    full_profitability_roi: Decimal = Decimal("0.20")
    full_liquidity_quantity: Decimal = Decimal("40")
    full_mad_ratio: Decimal = Decimal("0.20")
    full_drawdown_ratio: Decimal = Decimal("0.35")
    full_spread_ratio: Decimal = Decimal("0.25")
    profitability_weight: Decimal = Decimal("40")
    liquidity_weight: Decimal = Decimal("20")
    stability_weight: Decimal = Decimal("15")
    activity_weight: Decimal = Decimal("10")
    confidence_weight: Decimal = Decimal("10")
    freshness_weight: Decimal = Decimal("5")
    risk_penalty_weight: Decimal = Decimal("0.20")
    fee_rate: Decimal = Decimal("0.15")
    currency_quantum: Decimal = Decimal("0.01")
    absorption_withhold_policy: AbsorptionWithholdPolicy = "LEGACY_ONLY"
    channel_coverage_policy: ChannelCoveragePolicy = "IGNORE"

    def __post_init__(self) -> None:
        if self.maximum_current_market_age <= timedelta(0):
            raise OpportunityV2ContractError("maximum_current_market_age must be positive.")
        if self.maximum_historical_trade_age <= timedelta(0):
            raise OpportunityV2ContractError("maximum_historical_trade_age must be positive.")
        if self.minimum_historical_bucket_count <= 0:
            raise OpportunityV2ContractError("minimum_historical_bucket_count must be positive.")
        if self.minimum_effective_day_count <= 0:
            raise OpportunityV2ContractError("minimum_effective_day_count must be positive.")
        decimal_fields = (
            self.minimum_observed_span_ratio,
            self.full_profitability_roi,
            self.full_liquidity_quantity,
            self.full_mad_ratio,
            self.full_drawdown_ratio,
            self.full_spread_ratio,
            self.profitability_weight,
            self.liquidity_weight,
            self.stability_weight,
            self.activity_weight,
            self.confidence_weight,
            self.freshness_weight,
            self.risk_penalty_weight,
            self.fee_rate,
            self.currency_quantum,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in decimal_fields):
            raise OpportunityV2ContractError("all numeric configuration values must be Decimal.")
        weights = (
            self.profitability_weight,
            self.liquidity_weight,
            self.stability_weight,
            self.activity_weight,
            self.confidence_weight,
            self.freshness_weight,
        )
        if sum(weights, Decimal("0")) != Decimal("100"):
            raise OpportunityV2ContractError("component weights must sum to 100.")
        if not Decimal("0") < self.minimum_observed_span_ratio <= Decimal("1"):
            raise OpportunityV2ContractError("minimum_observed_span_ratio must be in (0, 1].")
        if not Decimal("0") <= self.risk_penalty_weight <= Decimal("1"):
            raise OpportunityV2ContractError("risk_penalty_weight must be in [0, 1].")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise OpportunityV2ContractError("fee_rate must be in [0, 1).")
        if self.currency_quantum <= Decimal("0"):
            raise OpportunityV2ContractError("currency_quantum must be positive.")
        if self.absorption_withhold_policy not in {
            "LEGACY_ONLY",
            "EVIDENCE_EXTENDS_LEGACY",
            "EVIDENCE_REPLACES_LEGACY",
        }:
            raise OpportunityV2ContractError("unsupported absorption_withhold_policy.")
        if self.channel_coverage_policy not in {
            "IGNORE",
            "WITHHOLD_UNCATALOGUED",
            "WITHHOLD_UNMODELLED_SUPPLY",
            "WITHHOLD_UNREVIEWED",
        }:
            raise OpportunityV2ContractError("unsupported channel_coverage_policy.")


@dataclass(frozen=True, slots=True)
class OpportunityFeaturesV2:
    historical_bucket_count: int
    historical_effective_day_count: int
    hourly_bucket_count: int
    daily_bucket_count: int
    historical_start_at: datetime | None
    historical_end_at: datetime | None
    historical_latest_age_seconds: int | None
    observed_span_ratio: Decimal
    historical_price_median: Decimal | None
    context_weighted_historical_price_median: Decimal | None
    historical_price_mad: Decimal | None
    historical_mad_ratio: Decimal | None
    context_weighted_historical_price_mad: Decimal | None
    context_weighted_historical_mad_ratio: Decimal | None
    historical_max_drawdown: Decimal | None
    median_reported_trade_volume: Decimal | None
    manual_strength_index: Decimal
    manual_attention_index: Decimal
    manual_context_defaulted: bool
    historical_context_comparability: Decimal
    historical_context_effective_day_count: Decimal
    historical_context_effective_sample_size: Decimal
    historical_context_high_quality_day_count: Decimal
    historical_profitable_context_weight_share: Decimal | None
    current_spread_ratio: Decimal | None
    liquidity_source: LiquiditySource
    liquidity_quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class OpportunityScoreV2Result:
    item_id: int
    as_of: datetime
    eligible: bool
    score_published: bool
    score: Decimal | None
    raw_score: Decimal | None
    profitability_score: Decimal | None
    liquidity_score: Decimal | None
    stability_score: Decimal | None
    activity_score: Decimal | None
    data_confidence_score: Decimal | None
    freshness_score: Decimal | None
    risk_penalty: Decimal | None
    current_ask: Decimal | None
    current_bid: Decimal | None
    reference_sell_price: Decimal | None
    reference_sell_method: str | None
    reference_sell_version: str | None
    sale_proceeds: Decimal | None
    fee_amount: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    features: OpportunityFeaturesV2
    supply_cycle_state: str
    supply_pool_family: str
    supply_coupon_mode: str
    supply_confirmed_current_membership: bool
    supply_observed_return_count: int
    supply_observed_tradeable_return_count: int
    supply_eligible_family_event_count: int
    supply_recurrence_rate: Decimal | None
    supply_current_event_key: str | None
    supply_current_event_opens_at: datetime | None
    supply_current_event_closes_at: datetime | None
    reissue_known_return_count: int
    reissue_completed_tradeable_supply_count: int
    reissue_completed_account_only_return_count: int
    reissue_distinct_family_count: int
    reissue_trailing_1095d_tradeable_supply_count: int
    reissue_days_since_last_tradeable_supply_close: Decimal | None
    strategy_name: str
    strategy_version: str
    feature_version: str
    explanation_codes: tuple[str, ...]


class OpportunityScoreV2:
    strategy_name = "historical_trade_opportunity"
    strategy_version = "2.3.0"
    feature_version = "opportunity_features_v2_3"
    reference_sell_method = "context_weighted_historical_trade_median"
    reference_sell_version = "2.0.0"

    # Pre-close regimes are withheld unconditionally: supply is still being
    # announced or injected, so no post-event absorption evidence exists yet.
    # ``POST_EVENT_ABSORPTION`` is handled separately because it is the one
    # regime whose duration can be decided from the item's own trade evidence.
    _PRE_CLOSE_WITHHELD_SUPPLY_STATES = {
        "SEASONAL_WINDOW_APPROACHING",
        "ANNOUNCED_PRE_OPEN",
        "ACTIVE_SUPPLY_INJECTION",
    }
    _WITHHELD_SUPPLY_STATES = _PRE_CLOSE_WITHHELD_SUPPLY_STATES | {
        "POST_EVENT_ABSORPTION",
    }

    def __init__(self, config: OpportunityV2Config | None = None) -> None:
        self.config = config or OpportunityV2Config()

    def score(
        self,
        *,
        item_id: int,
        as_of: datetime,
        window_start: datetime,
        current_market: CurrentMarketEvidence | None,
        historical_buckets: Iterable[HistoricalTradeBucketEvidence],
        supply_cycle: SupplyCycleFeatures | None = None,
        reissue_exposure: ReissueExposureFeatures | None = None,
        manual_context_observations: Iterable[ManualMarketContextObservation] = (),
        absorption_exit: SupplyAbsorptionExitSnapshot | None = None,
        supply_channel_coverage: SupplyChannelCoverage | None = None,
    ) -> OpportunityScoreV2Result:
        as_of = _utc(as_of, "as_of")
        window_start = _utc(window_start, "window_start")
        if current_market is not None and current_market.item_id != item_id:
            raise OpportunityV2ContractError("current_market item_id does not match.")
        if supply_cycle is not None and supply_cycle.item_id != item_id:
            raise OpportunityV2ContractError("supply_cycle item_id does not match.")
        if reissue_exposure is not None and reissue_exposure.item_id != item_id:
            raise OpportunityV2ContractError("reissue_exposure item_id does not match.")
        if absorption_exit is not None:
            if absorption_exit.item_id != item_id:
                raise OpportunityV2ContractError("absorption_exit item_id does not match.")
            if absorption_exit.as_of > as_of:
                raise OpportunityV2ContractError(
                    "absorption_exit must not contain future information."
                )
        context_observations = tuple(manual_context_observations)
        if any(value.item_id != item_id for value in context_observations):
            raise OpportunityV2ContractError("manual context item_id does not match.")

        evidence = select_historical_trade_evidence(
            historical_buckets,
            as_of=as_of,
            window_start=window_start,
        )
        if evidence.item_id not in {0, item_id}:
            raise OpportunityV2ContractError("historical bucket item_id does not match.")
        features = self._features(
            item_id=item_id,
            as_of=as_of,
            window_start=window_start,
            current_market=current_market,
            evidence=evidence,
            manual_context_observations=context_observations,
        )
        codes = self._data_quality_codes(
            as_of=as_of,
            current_market=current_market,
            evidence=evidence,
            features=features,
        )

        cycle_state = supply_cycle.state if supply_cycle is not None else "UNKNOWN"
        pool_family = supply_cycle.pool_family if supply_cycle is not None else "UNKNOWN"
        coupon_mode = supply_cycle.coupon_mode if supply_cycle is not None else "UNKNOWN"
        confirmed_current_membership = (
            supply_cycle.confirmed_current_membership if supply_cycle is not None else False
        )
        observed_return_count = supply_cycle.observed_return_count if supply_cycle is not None else 0
        observed_tradeable_return_count = (
            supply_cycle.observed_tradeable_return_count if supply_cycle is not None else 0
        )
        eligible_family_event_count = (
            supply_cycle.eligible_family_event_count if supply_cycle is not None else 0
        )
        recurrence_rate = supply_cycle.recurrence_rate if supply_cycle is not None else None
        current_event_key = supply_cycle.current_event_key if supply_cycle is not None else None
        current_event_opens_at = (
            supply_cycle.current_event_opens_at if supply_cycle is not None else None
        )
        current_event_closes_at = (
            supply_cycle.current_event_closes_at if supply_cycle is not None else None
        )
        reissue_known_return_count = (
            reissue_exposure.known_return_count if reissue_exposure is not None else 0
        )
        reissue_completed_tradeable_supply_count = (
            reissue_exposure.completed_tradeable_supply_count
            if reissue_exposure is not None
            else 0
        )
        reissue_completed_account_only_return_count = (
            reissue_exposure.completed_account_only_return_count
            if reissue_exposure is not None
            else 0
        )
        reissue_distinct_family_count = (
            reissue_exposure.distinct_family_count if reissue_exposure is not None else 0
        )
        reissue_trailing_1095d_tradeable_supply_count = (
            reissue_exposure.trailing_1095d_tradeable_supply_count
            if reissue_exposure is not None
            else 0
        )
        reissue_days_since_last_tradeable_supply_close = (
            reissue_exposure.days_since_last_tradeable_supply_close
            if reissue_exposure is not None
            else None
        )
        if supply_cycle is not None:
            codes.extend(supply_cycle.explanation_codes)
        if reissue_exposure is not None:
            codes.extend(reissue_exposure.explanation_codes)
            codes.append("reissue_exposure_diagnostic_only_no_score_adjustment")
        if features.manual_context_defaulted:
            codes.append("manual_context_default_neutral")
        else:
            codes.append("manual_context_applied")
        if features.historical_context_comparability < Decimal("1"):
            codes.append("historical_context_comparability_reduced")
        if (
            features.historical_context_high_quality_day_count
            < features.historical_context_effective_day_count
        ):
            codes.append("historical_context_quality_discount_applied")
        absorption_decision = resolve_absorption_withholding(
            legacy_absorption_active=cycle_state == "POST_EVENT_ABSORPTION",
            snapshot=absorption_exit,
            policy=self.config.absorption_withhold_policy,
        )
        if (
            absorption_exit is not None
            or self.config.absorption_withhold_policy != "LEGACY_ONLY"
        ):
            codes.extend(absorption_decision.explanation_codes)
        if (
            cycle_state in self._PRE_CLOSE_WITHHELD_SUPPLY_STATES
            or absorption_decision.withhold
        ):
            codes.append("strategy_withheld_for_supply_cycle_regime")

        channel_decision = resolve_channel_coverage_withholding(
            coverage=supply_channel_coverage,
            policy=self.config.channel_coverage_policy,
        )
        if (
            supply_channel_coverage is not None
            or self.config.channel_coverage_policy != "IGNORE"
        ):
            codes.extend(channel_decision.explanation_codes)
        if channel_decision.withhold:
            codes.append("strategy_withheld_for_unmodelled_supply_channel")

        publishable = not any(
            code.startswith("missing_")
            or code.startswith("stale_")
            or code.startswith("insufficient_")
            or code == "strategy_withheld_for_supply_cycle_regime"
            or code == "strategy_withheld_for_unmodelled_supply_channel"
            for code in codes
        )
        if not publishable:
            return self._unpublished(
                item_id=item_id,
                as_of=as_of,
                current_market=current_market,
                features=features,
                cycle_state=cycle_state,
                pool_family=pool_family,
                coupon_mode=coupon_mode,
                confirmed_current_membership=confirmed_current_membership,
                observed_return_count=observed_return_count,
                observed_tradeable_return_count=observed_tradeable_return_count,
                eligible_family_event_count=eligible_family_event_count,
                recurrence_rate=recurrence_rate,
                current_event_key=current_event_key,
                current_event_opens_at=current_event_opens_at,
                current_event_closes_at=current_event_closes_at,
                reissue_known_return_count=reissue_known_return_count,
                reissue_completed_tradeable_supply_count=reissue_completed_tradeable_supply_count,
                reissue_completed_account_only_return_count=reissue_completed_account_only_return_count,
                reissue_distinct_family_count=reissue_distinct_family_count,
                reissue_trailing_1095d_tradeable_supply_count=reissue_trailing_1095d_tradeable_supply_count,
                reissue_days_since_last_tradeable_supply_close=reissue_days_since_last_tradeable_supply_close,
                codes=codes,
            )

        current_ask = current_market.best_ask if current_market else None
        current_bid = current_market.best_bid if current_market else None
        reference = features.context_weighted_historical_price_median
        assert current_ask is not None and reference is not None
        sale_proceeds = _sale_proceeds(reference, self.config.fee_rate, self.config.currency_quantum)
        fee_amount = reference - sale_proceeds
        net_profit = sale_proceeds - current_ask
        net_roi = net_profit / current_ask

        roi_profitability = _clamp100(
            max(Decimal("0"), net_roi) / self.config.full_profitability_roi * Decimal("100")
        )
        profitable_share = features.historical_profitable_context_weight_share or Decimal("0")
        profitability = roi_profitability * profitable_share
        liquidity = _clamp100(
            (features.liquidity_quantity or Decimal("0"))
            / self.config.full_liquidity_quantity
            * Decimal("100")
        )
        stability = self._stability_score(features)
        reliable_context_sample = features.historical_context_high_quality_day_count
        activity = _clamp100(
            reliable_context_sample
            / Decimal(self.config.minimum_effective_day_count * 2)
            * Decimal("100")
        )
        confidence = self._confidence_score(features)
        freshness = self._freshness_score(features)
        risk_penalty = self._risk_penalty(features)

        raw = (
            profitability * self.config.profitability_weight
            + liquidity * self.config.liquidity_weight
            + stability * self.config.stability_weight
            + activity * self.config.activity_weight
            + confidence * self.config.confidence_weight
            + freshness * self.config.freshness_weight
        ) / Decimal("100")
        score = _quantize(_clamp100(raw - risk_penalty))
        raw = _quantize(_clamp100(raw))
        eligible = (
            net_profit > Decimal("0")
            and net_roi > Decimal("0")
            and profitable_share > Decimal("0.5")
        )
        codes.append("historical_reference_available")
        codes.append(
            "historical_profitability_majority_support"
            if profitable_share > Decimal("0.5")
            else "historical_profitability_majority_not_supported"
        )
        codes.append("positive_net_profit" if net_profit > Decimal("0") else "non_positive_net_profit")
        codes.append("eligible_positive_after_fee_opportunity" if eligible else "not_eligible_for_opportunity_ranking")

        return OpportunityScoreV2Result(
            item_id=item_id,
            as_of=as_of,
            eligible=eligible,
            score_published=True,
            score=score,
            raw_score=raw,
            profitability_score=_quantize(profitability),
            liquidity_score=_quantize(liquidity),
            stability_score=_quantize(stability),
            activity_score=_quantize(activity),
            data_confidence_score=_quantize(confidence),
            freshness_score=_quantize(freshness),
            risk_penalty=_quantize(risk_penalty),
            current_ask=current_ask,
            current_bid=current_bid,
            reference_sell_price=reference,
            reference_sell_method=self.reference_sell_method,
            reference_sell_version=self.reference_sell_version,
            sale_proceeds=sale_proceeds,
            fee_amount=fee_amount,
            net_profit=net_profit,
            net_roi=net_roi,
            features=features,
            supply_cycle_state=cycle_state,
            supply_pool_family=pool_family,
            supply_coupon_mode=coupon_mode,
            supply_confirmed_current_membership=confirmed_current_membership,
            supply_observed_return_count=observed_return_count,
            supply_observed_tradeable_return_count=observed_tradeable_return_count,
            supply_eligible_family_event_count=eligible_family_event_count,
            supply_recurrence_rate=recurrence_rate,
            supply_current_event_key=current_event_key,
            supply_current_event_opens_at=current_event_opens_at,
            supply_current_event_closes_at=current_event_closes_at,
            reissue_known_return_count=reissue_known_return_count,
            reissue_completed_tradeable_supply_count=reissue_completed_tradeable_supply_count,
            reissue_completed_account_only_return_count=reissue_completed_account_only_return_count,
            reissue_distinct_family_count=reissue_distinct_family_count,
            reissue_trailing_1095d_tradeable_supply_count=reissue_trailing_1095d_tradeable_supply_count,
            reissue_days_since_last_tradeable_supply_close=reissue_days_since_last_tradeable_supply_close,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            explanation_codes=tuple(dict.fromkeys(codes)),
        )

    def _features(
        self,
        *,
        item_id: int,
        as_of: datetime,
        window_start: datetime,
        current_market: CurrentMarketEvidence | None,
        evidence: HistoricalTradeEvidence,
        manual_context_observations: tuple[ManualMarketContextObservation, ...],
    ) -> OpportunityFeaturesV2:
        daily_series = aggregate_trade_evidence_by_utc_day(evidence.buckets)
        prices = tuple(value.vwap_price for value in daily_series)
        volumes = tuple(value.reported_trade_volume for value in daily_series)
        median = _median(prices)
        current_context = resolve_manual_market_context(
            item_id=item_id,
            effective_at=as_of,
            knowledge_as_of=as_of,
            observations=manual_context_observations,
        )
        contextual_prices: list[tuple[Decimal, Decimal]] = []
        context_weights: list[Decimal] = []
        for point in daily_series:
            historical_context = resolve_manual_market_context(
                item_id=current_context.item_id,
                effective_at=point.available_at,
                knowledge_as_of=as_of,
                observations=manual_context_observations,
            )
            comparability = context_comparability(current_context, historical_context)
            contextual_prices.append((point.vwap_price, comparability.combined_similarity))
            context_weights.append(comparability.combined_similarity)
        context_weighted_median = weighted_decimal_median(contextual_prices)
        context_weighted_mad = (
            weighted_decimal_median(
                tuple(
                    (
                        abs(point.vwap_price - context_weighted_median),
                        weight,
                    )
                    for point, weight in zip(daily_series, context_weights, strict=True)
                )
            )
            if context_weighted_median is not None
            else None
        )
        context_weighted_mad_ratio = (
            context_weighted_mad / context_weighted_median
            if context_weighted_mad is not None and context_weighted_median
            else None
        )
        context_effective_day_count = sum(context_weights, Decimal("0"))
        context_weight_square_sum = sum((weight * weight for weight in context_weights), Decimal("0"))
        context_effective_sample_size = (
            context_effective_day_count * context_effective_day_count / context_weight_square_sum
            if context_weight_square_sum > Decimal("0")
            else Decimal("0")
        )
        profitable_context_weight = Decimal("0")
        current_ask = current_market.best_ask if current_market is not None else None
        if current_ask is not None:
            for point, weight in zip(daily_series, context_weights, strict=True):
                proceeds = _sale_proceeds(
                    point.vwap_price,
                    self.config.fee_rate,
                    self.config.currency_quantum,
                )
                if proceeds > current_ask:
                    profitable_context_weight += weight
        profitable_context_weight_share = (
            profitable_context_weight / context_effective_day_count
            if current_ask is not None and context_effective_day_count > Decimal("0")
            else None
        )
        historical_context_comparability = (
            context_effective_day_count / Decimal(len(context_weights))
            if context_weights
            else Decimal("1")
        )
        mad = _median(tuple(abs(value - median) for value in prices)) if median is not None else None
        mad_ratio = mad / median if mad is not None and median else None
        max_drawdown = _maximum_drawdown(prices)
        spread = None
        if current_market and current_market.best_ask and current_market.best_bid:
            spread = (current_market.best_ask - current_market.best_bid) / current_market.best_bid
        liquidity_source: LiquiditySource = "unavailable"
        liquidity_quantity = None
        if (
            current_market
            and current_market.observed_ask_quantity is not None
            and current_market.observed_bid_quantity is not None
        ):
            liquidity_source = "reviewed_screenshot_quantity"
            liquidity_quantity = Decimal(
                current_market.observed_ask_quantity + current_market.observed_bid_quantity
            )
        elif (
            current_market
            and current_market.ask_count is not None
            and current_market.bid_count is not None
        ):
            liquidity_source = "snapshot_counts"
            liquidity_quantity = Decimal(current_market.ask_count + current_market.bid_count)

        window_seconds = Decimal(str((as_of - window_start).total_seconds()))
        span_seconds = Decimal(str(evidence.observed_span.total_seconds()))
        span_ratio = Decimal("0") if window_seconds <= 0 else min(Decimal("1"), span_seconds / window_seconds)
        latest_age = (
            int((as_of - evidence.latest_trade_at).total_seconds())
            if evidence.latest_trade_at is not None
            else None
        )
        return OpportunityFeaturesV2(
            historical_bucket_count=len(evidence.buckets),
            historical_effective_day_count=len(daily_series),
            hourly_bucket_count=evidence.hourly_bucket_count,
            daily_bucket_count=evidence.daily_bucket_count,
            historical_start_at=evidence.first_bucket_at,
            historical_end_at=evidence.last_bucket_end_at,
            historical_latest_age_seconds=latest_age,
            observed_span_ratio=span_ratio,
            historical_price_median=median,
            context_weighted_historical_price_median=context_weighted_median,
            historical_price_mad=mad,
            historical_mad_ratio=mad_ratio,
            context_weighted_historical_price_mad=context_weighted_mad,
            context_weighted_historical_mad_ratio=context_weighted_mad_ratio,
            historical_max_drawdown=max_drawdown,
            median_reported_trade_volume=_median(volumes),
            manual_strength_index=current_context.strength_index,
            manual_attention_index=current_context.attention_index,
            manual_context_defaulted=current_context.defaulted,
            historical_context_comparability=historical_context_comparability,
            historical_context_effective_day_count=context_effective_day_count,
            historical_context_effective_sample_size=context_effective_sample_size,
            historical_context_high_quality_day_count=context_weight_square_sum,
            historical_profitable_context_weight_share=profitable_context_weight_share,
            current_spread_ratio=spread,
            liquidity_source=liquidity_source,
            liquidity_quantity=liquidity_quantity,
        )

    def _data_quality_codes(
        self,
        *,
        as_of: datetime,
        current_market: CurrentMarketEvidence | None,
        evidence: HistoricalTradeEvidence,
        features: OpportunityFeaturesV2,
    ) -> list[str]:
        codes: list[str] = []
        if current_market is None:
            codes.append("missing_current_market")
        else:
            if current_market.observed_at > as_of:
                raise OpportunityV2ContractError("current market evidence must not be in the future.")
            if current_market.best_ask is None:
                codes.append("missing_current_ask")
            if as_of - current_market.observed_at > self.config.maximum_current_market_age:
                codes.append("stale_current_market")
        if not evidence.buckets:
            codes.append("missing_historical_trade_evidence")
        if features.historical_bucket_count < self.config.minimum_historical_bucket_count:
            codes.append("insufficient_historical_bucket_count")
        if features.historical_effective_day_count < self.config.minimum_effective_day_count:
            codes.append("insufficient_historical_day_count")
        if (
            features.historical_context_effective_day_count
            < Decimal(self.config.minimum_effective_day_count)
        ):
            codes.append("insufficient_context_comparable_history")
        if features.observed_span_ratio < self.config.minimum_observed_span_ratio:
            codes.append("insufficient_historical_time_coverage")
        if features.historical_end_at is None:
            codes.append("missing_historical_trade_freshness")
        elif as_of - features.historical_end_at > self.config.maximum_historical_trade_age:
            codes.append("stale_historical_trade_evidence")
        if features.context_weighted_historical_price_median is None:
            codes.append("missing_historical_reference")
        if features.liquidity_source == "unavailable":
            codes.append("missing_current_liquidity_evidence")
        return codes

    def _stability_score(self, features: OpportunityFeaturesV2) -> Decimal:
        if features.context_weighted_historical_mad_ratio is None:
            return Decimal("0")
        return Decimal("100") - _clamp100(
            features.context_weighted_historical_mad_ratio
            / self.config.full_mad_ratio
            * Decimal("100")
        )

    def _confidence_score(self, features: OpportunityFeaturesV2) -> Decimal:
        reliable_context_sample = features.historical_context_high_quality_day_count
        count = _clamp100(
            reliable_context_sample
            / Decimal(self.config.minimum_effective_day_count * 2)
            * Decimal("100")
        )
        span = _clamp100(
            features.observed_span_ratio / self.config.minimum_observed_span_ratio * Decimal("100")
        )
        granularity = Decimal("100") if features.hourly_bucket_count > 0 else Decimal("70")
        return (count + span + granularity) / Decimal("3")

    def _freshness_score(self, features: OpportunityFeaturesV2) -> Decimal:
        if features.historical_latest_age_seconds is None:
            return Decimal("0")
        maximum = Decimal(str(self.config.maximum_historical_trade_age.total_seconds()))
        age = Decimal(features.historical_latest_age_seconds)
        return _clamp100((Decimal("1") - age / maximum) * Decimal("100"))

    def _risk_penalty(self, features: OpportunityFeaturesV2) -> Decimal:
        spread_risk = (
            Decimal("0")
            if features.current_spread_ratio is None
            else _clamp100(
                max(Decimal("0"), features.current_spread_ratio)
                / self.config.full_spread_ratio
                * Decimal("100")
            )
        )
        drawdown_risk = (
            Decimal("0")
            if features.historical_max_drawdown is None
            else _clamp100(
                features.historical_max_drawdown
                / self.config.full_drawdown_ratio
                * Decimal("100")
            )
        )
        return ((spread_risk + drawdown_risk) / Decimal("2")) * self.config.risk_penalty_weight

    def _unpublished(
        self,
        *,
        item_id: int,
        as_of: datetime,
        current_market: CurrentMarketEvidence | None,
        features: OpportunityFeaturesV2,
        cycle_state: str,
        pool_family: str,
        coupon_mode: str,
        confirmed_current_membership: bool,
        observed_return_count: int,
        observed_tradeable_return_count: int,
        eligible_family_event_count: int,
        recurrence_rate: Decimal | None,
        current_event_key: str | None,
        current_event_opens_at: datetime | None,
        current_event_closes_at: datetime | None,
        reissue_known_return_count: int,
        reissue_completed_tradeable_supply_count: int,
        reissue_completed_account_only_return_count: int,
        reissue_distinct_family_count: int,
        reissue_trailing_1095d_tradeable_supply_count: int,
        reissue_days_since_last_tradeable_supply_close: Decimal | None,
        codes: list[str],
    ) -> OpportunityScoreV2Result:
        codes.append("score_not_published")
        return OpportunityScoreV2Result(
            item_id=item_id,
            as_of=as_of,
            eligible=False,
            score_published=False,
            score=None,
            raw_score=None,
            profitability_score=None,
            liquidity_score=None,
            stability_score=None,
            activity_score=None,
            data_confidence_score=None,
            freshness_score=None,
            risk_penalty=None,
            current_ask=current_market.best_ask if current_market else None,
            current_bid=current_market.best_bid if current_market else None,
            reference_sell_price=None,
            reference_sell_method=None,
            reference_sell_version=None,
            sale_proceeds=None,
            fee_amount=None,
            net_profit=None,
            net_roi=None,
            features=features,
            supply_cycle_state=cycle_state,
            supply_pool_family=pool_family,
            supply_coupon_mode=coupon_mode,
            supply_confirmed_current_membership=confirmed_current_membership,
            supply_observed_return_count=observed_return_count,
            supply_observed_tradeable_return_count=observed_tradeable_return_count,
            supply_eligible_family_event_count=eligible_family_event_count,
            supply_recurrence_rate=recurrence_rate,
            supply_current_event_key=current_event_key,
            supply_current_event_opens_at=current_event_opens_at,
            supply_current_event_closes_at=current_event_closes_at,
            reissue_known_return_count=reissue_known_return_count,
            reissue_completed_tradeable_supply_count=reissue_completed_tradeable_supply_count,
            reissue_completed_account_only_return_count=reissue_completed_account_only_return_count,
            reissue_distinct_family_count=reissue_distinct_family_count,
            reissue_trailing_1095d_tradeable_supply_count=reissue_trailing_1095d_tradeable_supply_count,
            reissue_days_since_last_tradeable_supply_close=reissue_days_since_last_tradeable_supply_close,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            explanation_codes=tuple(dict.fromkeys(codes)),
        )


def rank_opportunity_scores_v2(
    opportunities: Iterable[OpportunityScoreV2Result],
    *,
    eligible_only: bool = True,
    minimum_score: Decimal = Decimal("0"),
) -> tuple[OpportunityScoreV2Result, ...]:
    if not isinstance(minimum_score, Decimal) or not minimum_score.is_finite():
        raise OpportunityV2ContractError("minimum_score must be a finite Decimal.")
    if not Decimal("0") <= minimum_score <= Decimal("100"):
        raise OpportunityV2ContractError("minimum_score must be in [0, 100].")
    values = tuple(opportunities)
    ids = [value.item_id for value in values]
    if len(ids) != len(set(ids)):
        raise OpportunityV2ContractError("item_id values must be unique for ranking.")
    filtered = tuple(
        value
        for value in values
        if (not eligible_only or value.eligible)
        and (
            value.score >= minimum_score
            if value.score is not None
            else (not eligible_only and minimum_score == Decimal("0"))
        )
    )
    return tuple(
        sorted(
            filtered,
            key=lambda value: (
                0 if value.eligible else 1,
                0 if value.score_published else 1,
                -(value.score or Decimal("-1")),
                -(value.profitability_score or Decimal("-1")),
                value.item_id,
            ),
        )
    )



def _sale_proceeds(price: Decimal, fee_rate: Decimal, quantum: Decimal) -> Decimal:
    raw = price * (Decimal("1") - fee_rate)
    units = (raw / quantum).to_integral_value(rounding=ROUND_DOWN)
    return (units * quantum).quantize(quantum)


def _maximum_drawdown(prices: tuple[Decimal, ...]) -> Decimal | None:
    if not prices:
        return None
    peak = prices[0]
    maximum = Decimal("0")
    for value in prices[1:]:
        if value > peak:
            peak = value
            continue
        drawdown = (peak - value) / peak
        maximum = max(maximum, drawdown)
    return maximum


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _clamp100(value: Decimal) -> Decimal:
    return min(Decimal("100"), max(Decimal("0"), value))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityV2ContractError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
