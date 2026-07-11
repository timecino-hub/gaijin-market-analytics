from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from gaijin_market_analytics.backtesting.calibration_contracts import (
    CalibrationExitReason,
    CalibrationSplit,
    ItemMarketHistory,
    OpportunityCalibrationCase,
    OpportunityCalibrationConfig,
)
from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
    TRADE_EVIDENCE_ENGINE_NAME,
    TRADE_EVIDENCE_ENGINE_VERSION,
    TRADE_EVIDENCE_POLICY_NAME,
    TRADE_EVIDENCE_POLICY_VERSION,
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    TradeEvidenceCalibrationResult,
    TradeEvidenceCase,
    TradeEvidenceCohortSummary,
    TradeEvidenceConfig,
    TradeEvidenceLevel,
    TradeEvidenceSegmentDimension,
    TradeEvidenceSegmentSummary,
    TradeEvidenceView,
    TradeLiquidityBand,
    TradeMarketPhase,
)
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import FeePolicy, calculate_sale_proceeds


def build_trade_evidence_calibration(
    *,
    market_histories: tuple[ItemMarketHistory, ...],
    trade_histories: tuple[ItemHistoricalTradeHistory, ...],
    cases: tuple[OpportunityCalibrationCase, ...],
    calibration_config: OpportunityCalibrationConfig,
    trade_evidence_config: TradeEvidenceConfig,
    fee_policy: FeePolicy,
    base_configuration_sha256: str,
) -> TradeEvidenceCalibrationResult:
    """Build non-execution historical-trade diagnostics for calibration cases.

    Completed VWAP buckets provide evidence that market trades supported a target,
    but never assert that the user's order filled or held queue priority.
    """

    item_ids = [history.item_id for history in trade_histories]
    if len(item_ids) != len(set(item_ids)):
        raise ContractValidationError(
            "Trade evidence histories require unique item_id values."
        )
    by_item = {history.item_id: history for history in trade_histories}
    evidence_cases = tuple(
        _build_case(
            case=case,
            history=by_item.get(case.item_id),
            config=trade_evidence_config,
            fee_policy=fee_policy,
        )
        for case in cases
    )
    cohorts = _build_cohorts(evidence_cases)
    segments = _build_segments(
        evidence_cases,
        calibration_config=calibration_config,
        evidence_config=trade_evidence_config,
    )
    return TradeEvidenceCalibrationResult(
        config=trade_evidence_config,
        engine_name=TRADE_EVIDENCE_ENGINE_NAME,
        engine_version=TRADE_EVIDENCE_ENGINE_VERSION,
        policy_name=TRADE_EVIDENCE_POLICY_NAME,
        policy_version=TRADE_EVIDENCE_POLICY_VERSION,
        dataset_sha256=_evidence_dataset_sha256(market_histories, trade_histories),
        configuration_sha256=_evidence_configuration_sha256(
            base_configuration_sha256,
            trade_evidence_config,
        ),
        cases=evidence_cases,
        cohorts=cohorts,
        segments=segments,
    )


def _build_case(
    *,
    case: OpportunityCalibrationCase,
    history: ItemHistoricalTradeHistory | None,
    config: TradeEvidenceConfig,
    fee_policy: FeePolicy,
) -> TradeEvidenceCase:
    future_end = case.cutoff_as_of + timedelta(days=case.forward_horizon_days)
    selected = _select_completed_buckets(
        history.buckets if history is not None else (),
        start_at=case.cutoff_as_of,
        end_at=future_end,
    )
    hourly_count = sum(
        bucket.granularity is HistoricalTradeGranularity.HOUR_1
        for bucket in selected
    )
    daily_count = len(selected) - hourly_count
    target = case.target_exit_price
    vwap_candidates = tuple(
        bucket
        for bucket in selected
        if target is not None and bucket.reported_vwap_price >= target
    )
    volume_candidates = tuple(
        bucket
        for bucket in vwap_candidates
        if bucket.reported_trade_volume >= config.minimum_reported_trade_volume
    )
    selected_support = (
        volume_candidates[0]
        if volume_candidates
        else vwap_candidates[0]
        if vwap_candidates
        else None
    )
    quote_touch = case.exit_reason is CalibrationExitReason.TARGET_CONFIRMED
    trade_vwap_support = bool(vwap_candidates)
    trade_volume_support = bool(volume_candidates)
    combined_support = quote_touch and trade_vwap_support
    if trade_volume_support:
        strongest = TradeEvidenceLevel.TRADE_VOLUME_SUPPORT
    elif trade_vwap_support:
        strongest = TradeEvidenceLevel.TRADE_VWAP_SUPPORT
    elif quote_touch:
        strongest = TradeEvidenceLevel.QUOTE_TOUCH
    else:
        strongest = TradeEvidenceLevel.UNRESOLVED

    sale_proceeds = None
    net_profit = None
    net_roi = None
    if case.entry_executed and case.entry_price is not None and target is not None:
        sale_proceeds = calculate_sale_proceeds(target, fee_policy)
        net_profit = sale_proceeds - case.entry_price
        net_roi = net_profit / case.entry_price

    phase_buckets = _select_completed_buckets(
        history.buckets if history is not None else (),
        start_at=case.cutoff_as_of - timedelta(days=config.phase_lookback_days),
        end_at=case.cutoff_as_of,
    )
    return TradeEvidenceCase(
        item_id=case.item_id,
        cutoff_as_of=case.cutoff_as_of,
        split=case.split,
        forward_horizon_days=case.forward_horizon_days,
        entry_executed=case.entry_executed,
        entry_price=case.entry_price,
        target_exit_price=target,
        opportunity_eligible=case.opportunity_eligible,
        score=case.score,
        liquidity_score=case.liquidity_score,
        market_phase=_market_phase(phase_buckets, config),
        quote_touch=quote_touch,
        quote_touch_at=case.exit_at if quote_touch else None,
        trade_vwap_support=trade_vwap_support,
        trade_volume_support=trade_volume_support,
        trade_support_at=(
            selected_support.bucket_end_utc if selected_support is not None else None
        ),
        trade_support_granularity=(
            selected_support.granularity if selected_support is not None else None
        ),
        trade_support_vwap=(
            selected_support.reported_vwap_price if selected_support is not None else None
        ),
        trade_support_volume=(
            selected_support.reported_trade_volume if selected_support is not None else None
        ),
        selected_trade_bucket_count=len(selected),
        selected_hourly_bucket_count=hourly_count,
        selected_daily_fallback_count=daily_count,
        combined_support=combined_support,
        strongest_evidence=strongest,
        supported_sale_proceeds=sale_proceeds,
        supported_net_profit=net_profit,
        supported_net_roi=net_roi,
        positive_return=(net_profit > Decimal("0")) if net_profit is not None else None,
    )


def _select_completed_buckets(
    buckets: Iterable[HistoricalTradeBucket],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[HistoricalTradeBucket, ...]:
    eligible = tuple(
        bucket
        for bucket in buckets
        if bucket.bucket_start_utc >= start_at and bucket.bucket_end_utc <= end_at
    )
    hourly_days = {
        bucket.bucket_start_utc.date()
        for bucket in eligible
        if bucket.granularity is HistoricalTradeGranularity.HOUR_1
    }
    selected = tuple(
        bucket
        for bucket in eligible
        if bucket.granularity is HistoricalTradeGranularity.HOUR_1
        or bucket.bucket_start_utc.date() not in hourly_days
    )
    return tuple(
        sorted(
            selected,
            key=lambda value: (
                value.bucket_end_utc,
                value.bucket_duration_seconds,
            ),
        )
    )


def _market_phase(
    buckets: tuple[HistoricalTradeBucket, ...],
    config: TradeEvidenceConfig,
) -> TradeMarketPhase:
    if not buckets:
        return TradeMarketPhase.UNAVAILABLE
    if len(buckets) < config.minimum_phase_bucket_count:
        return TradeMarketPhase.THIN
    volumes = tuple(Decimal(bucket.reported_trade_volume) for bucket in buckets)
    median_volume = _median(volumes)
    if median_volume < Decimal(config.minimum_reported_trade_volume):
        return TradeMarketPhase.THIN

    latest = buckets[-1]
    prior = buckets[:-1]
    prior_prices = tuple(bucket.reported_vwap_price for bucket in prior)
    prior_volumes = tuple(Decimal(bucket.reported_trade_volume) for bucket in prior)
    if prior_prices and prior_volumes:
        if (
            latest.reported_vwap_price
            <= _median(prior_prices) * config.supply_shock_price_ratio
            and Decimal(latest.reported_trade_volume)
            >= _median(prior_volumes) * config.supply_shock_volume_multiplier
        ):
            return TradeMarketPhase.SUPPLY_SHOCK

    shock_index = _latest_shock_index(buckets, config)
    if shock_index is not None and shock_index < len(buckets) - 1:
        shock = buckets[shock_index]
        if latest.reported_vwap_price >= shock.reported_vwap_price * config.recovery_price_ratio:
            return TradeMarketPhase.RECOVERY
    return TradeMarketPhase.NORMAL


def _latest_shock_index(
    buckets: tuple[HistoricalTradeBucket, ...],
    config: TradeEvidenceConfig,
) -> int | None:
    latest_index = None
    minimum_history = max(2, config.minimum_phase_bucket_count - 1)
    for index in range(minimum_history, len(buckets)):
        prior = buckets[:index]
        candidate = buckets[index]
        median_price = _median(tuple(value.reported_vwap_price for value in prior))
        median_volume = _median(
            tuple(Decimal(value.reported_trade_volume) for value in prior)
        )
        if (
            candidate.reported_vwap_price
            <= median_price * config.supply_shock_price_ratio
            and Decimal(candidate.reported_trade_volume)
            >= median_volume * config.supply_shock_volume_multiplier
        ):
            latest_index = index
    return latest_index


def _build_cohorts(
    cases: tuple[TradeEvidenceCase, ...],
) -> tuple[TradeEvidenceCohortSummary, ...]:
    summaries = []
    horizons = tuple(sorted({case.forward_horizon_days for case in cases}))
    for split in (None, *tuple(CalibrationSplit)):
        for horizon in horizons:
            cohort = tuple(
                case
                for case in cases
                if case.forward_horizon_days == horizon
                and (split is None or case.split is split)
            )
            for view in TradeEvidenceView:
                summaries.append(_summarize_cohort(cohort, view, split, horizon))
    return tuple(summaries)


def _summarize_cohort(
    cases: tuple[TradeEvidenceCase, ...],
    view: TradeEvidenceView,
    split: CalibrationSplit | None,
    horizon: int,
) -> TradeEvidenceCohortSummary:
    evaluable = tuple(
        case
        for case in cases
        if case.entry_executed
        and case.entry_price is not None
        and case.target_exit_price is not None
    )
    supported = tuple(case for case in evaluable if _supports_view(case, view))
    rois = tuple(
        case.supported_net_roi
        for case in supported
        if case.supported_net_roi is not None
    )
    positive = sum(case.positive_return is True for case in supported)
    support_seconds = tuple(
        seconds
        for case in supported
        if (seconds := _time_to_support(case, view)) is not None
    )
    score_pairs = tuple(
        (case.score, case.supported_net_roi)
        for case in supported
        if case.score is not None and case.supported_net_roi is not None
    )
    return TradeEvidenceCohortSummary(
        view=view,
        split=split,
        forward_horizon_days=horizon,
        total_case_count=len(cases),
        evaluable_case_count=len(evaluable),
        supported_case_count=len(supported),
        volume_supported_count=sum(case.trade_volume_support for case in supported),
        eligible_supported_count=sum(case.opportunity_eligible is True for case in supported),
        positive_return_count=positive,
        support_rate=_rate(len(supported), len(evaluable)),
        positive_return_rate=_rate(positive, len(supported)),
        mean_supported_net_roi=_mean(rois),
        median_supported_net_roi=_median(rois) if rois else None,
        mean_time_to_support_seconds=(
            sum(support_seconds) // len(support_seconds) if support_seconds else None
        ),
        score_roi_spearman=_spearman(score_pairs),
    )


def _build_segments(
    cases: tuple[TradeEvidenceCase, ...],
    *,
    calibration_config: OpportunityCalibrationConfig,
    evidence_config: TradeEvidenceConfig,
) -> tuple[TradeEvidenceSegmentSummary, ...]:
    results = []
    horizons = tuple(sorted({case.forward_horizon_days for case in cases}))
    for split in (None, *tuple(CalibrationSplit)):
        for horizon in horizons:
            cohort = tuple(
                case
                for case in cases
                if case.forward_horizon_days == horizon
                and (split is None or case.split is split)
            )
            for view in TradeEvidenceView:
                for lower, upper in zip(
                    calibration_config.score_bin_edges,
                    calibration_config.score_bin_edges[1:],
                ):
                    includes_upper = upper == calibration_config.score_bin_edges[-1]
                    segment_cases = tuple(
                        case
                        for case in cohort
                        if case.score is not None
                        and case.score >= lower
                        and (case.score <= upper if includes_upper else case.score < upper)
                    )
                    results.append(
                        _segment_summary(
                            segment_cases,
                            view=view,
                            split=split,
                            horizon=horizon,
                            dimension=TradeEvidenceSegmentDimension.SCORE_BIN,
                            segment=f"{lower}-{upper}",
                            lower=lower,
                            upper=upper,
                            includes_upper=includes_upper,
                        )
                    )
                for band in TradeLiquidityBand:
                    segment_cases = tuple(
                        case
                        for case in cohort
                        if _liquidity_band(case.liquidity_score, evidence_config) is band
                    )
                    results.append(
                        _segment_summary(
                            segment_cases,
                            view=view,
                            split=split,
                            horizon=horizon,
                            dimension=TradeEvidenceSegmentDimension.LIQUIDITY_BAND,
                            segment=band.value,
                        )
                    )
                for phase in TradeMarketPhase:
                    segment_cases = tuple(
                        case for case in cohort if case.market_phase is phase
                    )
                    results.append(
                        _segment_summary(
                            segment_cases,
                            view=view,
                            split=split,
                            horizon=horizon,
                            dimension=TradeEvidenceSegmentDimension.MARKET_PHASE,
                            segment=phase.value,
                        )
                    )
    return tuple(results)


def _segment_summary(
    cases: tuple[TradeEvidenceCase, ...],
    *,
    view: TradeEvidenceView,
    split: CalibrationSplit | None,
    horizon: int,
    dimension: TradeEvidenceSegmentDimension,
    segment: str,
    lower: Decimal | None = None,
    upper: Decimal | None = None,
    includes_upper: bool = False,
) -> TradeEvidenceSegmentSummary:
    evaluable = tuple(
        case
        for case in cases
        if case.entry_executed
        and case.entry_price is not None
        and case.target_exit_price is not None
    )
    supported = tuple(case for case in evaluable if _supports_view(case, view))
    rois = tuple(
        case.supported_net_roi
        for case in supported
        if case.supported_net_roi is not None
    )
    positive = sum(case.positive_return is True for case in supported)
    return TradeEvidenceSegmentSummary(
        view=view,
        split=split,
        forward_horizon_days=horizon,
        dimension=dimension,
        segment=segment,
        lower_bound=lower,
        upper_bound=upper,
        includes_upper_bound=includes_upper,
        total_case_count=len(cases),
        evaluable_case_count=len(evaluable),
        supported_case_count=len(supported),
        volume_supported_count=sum(case.trade_volume_support for case in supported),
        positive_return_count=positive,
        support_rate=_rate(len(supported), len(evaluable)),
        positive_return_rate=_rate(positive, len(supported)),
        mean_supported_net_roi=_mean(rois),
    )


def _supports_view(case: TradeEvidenceCase, view: TradeEvidenceView) -> bool:
    if view is TradeEvidenceView.QUOTE_ONLY:
        return case.quote_touch
    if view is TradeEvidenceView.TRADE_SUPPORTED:
        return case.trade_vwap_support
    return case.combined_support


def _time_to_support(case: TradeEvidenceCase, view: TradeEvidenceView) -> int | None:
    if view is TradeEvidenceView.QUOTE_ONLY:
        at = case.quote_touch_at
    elif view is TradeEvidenceView.TRADE_SUPPORTED:
        at = case.trade_support_at
    else:
        values = tuple(
            value
            for value in (case.quote_touch_at, case.trade_support_at)
            if value is not None
        )
        at = max(values) if len(values) == 2 else None
    if at is None:
        return None
    return int((at - case.cutoff_as_of).total_seconds())


def _liquidity_band(
    value: Decimal | None,
    config: TradeEvidenceConfig,
) -> TradeLiquidityBand:
    if value is None:
        return TradeLiquidityBand.UNAVAILABLE
    lower, upper = config.liquidity_band_edges
    if value < lower:
        return TradeLiquidityBand.LOW
    if value < upper:
        return TradeLiquidityBand.MEDIUM
    return TradeLiquidityBand.HIGH


def _evidence_dataset_sha256(
    market_histories: tuple[ItemMarketHistory, ...],
    trade_histories: tuple[ItemHistoricalTradeHistory, ...],
) -> str:
    market_payload = []
    for history in sorted(market_histories, key=lambda value: value.item_id):
        market_payload.append(
            {
                "item_id": history.item_id,
                "observations": [
                    {
                        "observed_at": observation.observed_at.isoformat(),
                        "best_ask": _decimal_text(observation.best_ask),
                        "best_bid": _decimal_text(observation.best_bid),
                        "ask_count": observation.ask_count,
                        "bid_count": observation.bid_count,
                        "estimated_volume": _decimal_text(observation.estimated_volume),
                        "observation_key": observation.observation_key,
                    }
                    for observation in history.observations
                ],
            }
        )
    trade_payload = []
    for history in sorted(trade_histories, key=lambda value: value.item_id):
        trade_payload.append(
            {
                "item_id": history.item_id,
                "buckets": [
                    {
                        "granularity": bucket.granularity.value,
                        "bucket_start_utc": bucket.bucket_start_utc.isoformat(),
                        "bucket_duration_seconds": bucket.bucket_duration_seconds,
                        "reported_vwap_price": _decimal_text(
                            bucket.reported_vwap_price
                        ),
                        "reported_trade_volume": bucket.reported_trade_volume,
                        "price_semantics": bucket.price_semantics,
                        "volume_semantics": bucket.volume_semantics,
                        "source_schema_version": bucket.source_schema_version,
                    }
                    for bucket in history.buckets
                ],
            }
        )
    return _sha256_json({"market_histories": market_payload, "trade_histories": trade_payload})


def _evidence_configuration_sha256(
    base_configuration_sha256: str,
    config: TradeEvidenceConfig,
) -> str:
    return _sha256_json(
        {
            "base_configuration_sha256": base_configuration_sha256,
            "trade_evidence_config": _canonical_json_value(asdict(config)),
            "engine_name": TRADE_EVIDENCE_ENGINE_NAME,
            "engine_version": TRADE_EVIDENCE_ENGINE_VERSION,
            "policy_name": TRADE_EVIDENCE_POLICY_NAME,
            "policy_version": TRADE_EVIDENCE_POLICY_VERSION,
        }
    )


def _canonical_json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, tuple):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _rate(count: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(count) / Decimal(denominator)


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _spearman(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> Decimal | None:
    if len(pairs) < 3:
        return None
    x_ranks = _average_ranks(tuple(value[0] for value in pairs))
    y_ranks = _average_ranks(tuple(value[1] for value in pairs))
    x_mean = sum(x_ranks, Decimal("0")) / Decimal(len(x_ranks))
    y_mean = sum(y_ranks, Decimal("0")) / Decimal(len(y_ranks))
    covariance = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(x_ranks, y_ranks)
    )
    x_variance = sum((value - x_mean) ** 2 for value in x_ranks)
    y_variance = sum((value - y_mean) ** 2 for value in y_ranks)
    if x_variance == Decimal("0") or y_variance == Decimal("0"):
        return None
    return (covariance / (x_variance * y_variance).sqrt()).quantize(
        Decimal("0.0001")
    )


def _average_ranks(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    ordered = sorted(enumerate(values), key=lambda value: (value[1], value[0]))
    ranks = [Decimal("0")] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal("2")
        for original_index, _ in ordered[index:end]:
            ranks[original_index] = average_rank
        index = end
    return tuple(ranks)
