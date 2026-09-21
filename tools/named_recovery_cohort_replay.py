from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from gaijin_market_analytics.family_prior_generalization import (
    audit_family_prior_generalization,
    classify_generalization_aware_decision,
)
from gaijin_market_analytics.hierarchical_recovery import (
    HistoricalReturnOutcomeEvidence,
    corroborate_post_rerelease_signal,
    evaluate_sparse_baseline_family_fallback,
    estimate_family_recovery_prior,
    return_outcome_evidence,
)
from gaijin_market_analytics.absorption_exit import (
    ABSORPTION_WITHHOLD_POLICIES,
    measure_supply_absorption_exit,
)
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.reissue_exposure import (
    derive_reissue_exposure_features,
    merge_reissue_exposure_events,
    reissue_events_from_supply_pool,
)
from gaijin_market_analytics.reissue_exposure_catalog_v1 import (
    bind_supplemental_reissue_events,
)
from gaijin_market_analytics.policy_walk_forward import (
    PolicyDecisionRow,
    PolicyWalkForwardConfig,
    evaluate_policy_walk_forward,
)
from gaijin_market_analytics.rerelease_bottom_fishing import (
    CAUSAL_ENTRY_RULES,
    ENTRY_RULES,
    BottomFishingConfig,
    BottomFishingTrial,
    compare_bottom_fishing_rules,
    evaluate_bottom_fishing_outcome,
    resolve_bottom_fishing_entry,
)
from gaijin_market_analytics.walk_forward import (
    ResolvedRatioForecast,
    WalkForwardCalibrationConfig,
    evaluate_walk_forward_calibration,
)
from gaijin_market_analytics.price_distribution import (
    evaluate_price_distribution_outcome,
    family_ratio_observations,
    forecast_price_distribution,
    item_local_ratio_observations,
)
from gaijin_market_analytics.post_rerelease import (
    estimate_post_rerelease_reference,
    evaluate_post_rerelease_outcome,
    first_post_event_trade_proxy,
)
from gaijin_market_analytics.scarcity_rebuild import (
    evaluate_scarcity_rebuild_forward_outcome,
    measure_scarcity_rebuild,
)
from gaijin_market_analytics.supply_cycle_impacts import analyze_item_supply_cycle_impacts
from gaijin_market_analytics.supply_cycle_recovery import analyze_item_supply_recovery
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


MANIFEST_SCHEMA_VERSION = "named_recovery_cohort_manifest_v1"
REPORT_SCHEMA_VERSION = "named_recovery_cohort_report_v11"
# A short hold and a long one: entry timing and holding length are
# different levers and reporting only one would hide their interaction.
BOTTOM_FISHING_HOLDS = (30, 90)
PRICE_SCALE = Decimal("10000")
Role = Literal["calibration", "diagnostic", "negative_control"]


class CohortReplayError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ItemSpec:
    item_id: int
    item_name: str
    history_path: Path
    as_of: datetime
    role: Role


@dataclass(frozen=True, slots=True)
class BuiltExample:
    item_id: int
    item_name: str
    role: Role
    event_key: str
    family: str
    horizon_days: int
    estimate: Any
    outcome: Any
    item_local_ratio_observations: tuple[Any, ...]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline named-item supply-return cohort replay. Reads only local JSON "
            "history exports and never accesses Gaijin or any network endpoint."
        )
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_manifest(args.manifest)
    payload = json.dumps(_normalize(report), ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")


def run_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CohortReplayError("unsupported manifest schema_version")
    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise CohortReplayError("manifest items must be a non-empty list")
    specs = tuple(_parse_spec(value, path.parent) for value in raw_items)
    if len({value.item_id for value in specs}) != len(specs):
        raise CohortReplayError("item_id values must be unique")

    built: list[BuiltExample] = []
    bottom_fishing: dict[int, list[BottomFishingTrial]] = {
        value: [] for value in BOTTOM_FISHING_HOLDS
    }
    scarcity_examples: list[dict[str, Any]] = []
    item_reports: list[dict[str, Any]] = []
    reissue_events_by_item: dict[int, tuple[Any, ...]] = {}
    for spec in specs:
        buckets, source_meta = _load_history(spec)
        facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(
            item_id=spec.item_id,
            item_name=spec.item_name,
        )
        supply_reissues = reissue_events_from_supply_pool(
            item_id=spec.item_id,
            events=facts.events,
            memberships=facts.memberships,
        )
        supplemental_reissues = bind_supplemental_reissue_events(
            item_id=spec.item_id,
            item_name=spec.item_name,
        )
        reissue_events = merge_reissue_exposure_events(
            supply_reissues, supplemental_reissues
        )
        reissue_events_by_item[spec.item_id] = reissue_events
        current_exposure = derive_reissue_exposure_features(
            item_id=spec.item_id,
            as_of=spec.as_of,
            events=reissue_events,
        )
        item_report: dict[str, Any] = {
            "item_id": spec.item_id,
            "item_name": spec.item_name,
            "role": spec.role,
            "catalog_match_status": facts.match_status,
            "matched_catalog_name": facts.matched_catalog_name,
            "membership_count": len(facts.memberships),
            "reissue_exposure_current": asdict(current_exposure),
            "source": source_meta,
        }
        if facts.match_status != "MATCHED" or not facts.memberships:
            item_report.update(
                {
                    "event_impacts": [],
                    "recovery_profile": None,
                    "walk_forward_example_count": 0,
                    "explanation_codes": ["no_tradeable_supply_pool_history_bound"],
                }
            )
            item_reports.append(item_report)
            continue

        impacts = analyze_item_supply_cycle_impacts(
            item_id=spec.item_id,
            events=facts.events,
            memberships=facts.memberships,
            historical_buckets=buckets,
            as_of=spec.as_of,
        )
        recoveries = analyze_item_supply_recovery(
            item_id=spec.item_id,
            events=facts.events,
            memberships=facts.memberships,
            historical_buckets=buckets,
            as_of=spec.as_of,
        )
        member_keys = {value.event_key for value in facts.memberships}
        events = tuple(
            sorted(
                (value for value in facts.events if value.event_key in member_keys),
                key=lambda value: (value.opens_at, value.event_key),
            )
        )
        for index, event in enumerate(events):
            entry = first_post_event_trade_proxy(
                event=event,
                historical_buckets=buckets,
                as_of=spec.as_of,
            )
            if entry is None:
                continue
            prediction_as_of, entry_price = entry
            prediction_recovery = analyze_item_supply_recovery(
                item_id=spec.item_id,
                events=facts.events,
                memberships=facts.memberships,
                historical_buckets=buckets,
                as_of=prediction_as_of,
            )
            current_recovery = next(
                (
                    value
                    for value in prediction_recovery.recoveries
                    if value.event_key == event.event_key
                ),
                None,
            )
            if current_recovery is None:
                continue
            next_open = min(
                (
                    value.opens_at
                    for value in events
                    if value.family == event.family and value.opens_at > event.opens_at
                ),
                default=None,
            )
            for holding_days in BOTTOM_FISHING_HOLDS:
                config = BottomFishingConfig(holding_days=holding_days)
                for rule in ENTRY_RULES:
                    bottom_entry = resolve_bottom_fishing_entry(
                        rule=rule,
                        item_id=spec.item_id,
                        event=event,
                        historical_buckets=buckets,
                        as_of=spec.as_of,
                        config=config,
                        next_same_family_event_opens_at=next_open,
                    )
                    bottom_outcome = evaluate_bottom_fishing_outcome(
                        entry=bottom_entry,
                        historical_buckets=buckets,
                        as_of=spec.as_of,
                        config=config,
                        next_same_family_event_opens_at=next_open,
                    )
                    bottom_fishing[holding_days].append(
                        BottomFishingTrial(
                            entry=bottom_entry, outcome=bottom_outcome
                        )
                    )
            for horizon in (30, 60, 90, 180):
                ratio_observations = item_local_ratio_observations(
                    item_id=spec.item_id,
                    target_event_key=event.event_key,
                    family=event.family,
                    horizon_days=horizon,
                    prediction_as_of=prediction_as_of,
                    visible_before=event.opens_at,
                    recoveries=prediction_recovery.recoveries,
                )
                estimate = estimate_post_rerelease_reference(
                    event=event,
                    prediction_as_of=prediction_as_of,
                    horizon_days=horizon,
                    entry_proxy_price=entry_price,
                    current_recovery=current_recovery,
                    prior_recoveries=prediction_recovery.recoveries,
                )
                outcome = evaluate_post_rerelease_outcome(
                    estimate=estimate,
                    event=event,
                    historical_buckets=buckets,
                    evaluation_as_of=spec.as_of,
                    next_same_family_event_opens_at=next_open,
                )
                built.append(
                    BuiltExample(
                        item_id=spec.item_id,
                        item_name=spec.item_name,
                        role=spec.role,
                        event_key=event.event_key,
                        family=event.family,
                        horizon_days=horizon,
                        estimate=estimate,
                        outcome=outcome,
                        item_local_ratio_observations=ratio_observations,
                    )
                )
        recovery_by_key = {value.event_key: value for value in recoveries.recoveries}
        event_by_key = {value.event_key: value for value in events}
        current_scarcity_rebuild = None
        current_absorption_exit = None
        completed_events = tuple(value for value in events if value.closes_at <= spec.as_of)
        if completed_events:
            latest_event = max(completed_events, key=lambda value: (value.closes_at, value.event_key))
            latest_next_open = min(
                (
                    value.opens_at
                    for value in events
                    if value.family == latest_event.family and value.opens_at > latest_event.opens_at
                ),
                default=None,
            )
            latest_exposure = derive_reissue_exposure_features(
                item_id=spec.item_id,
                as_of=spec.as_of,
                events=reissue_events,
            )
            current_scarcity_rebuild = measure_scarcity_rebuild(
                item_id=spec.item_id,
                event=latest_event,
                historical_buckets=buckets,
                as_of=spec.as_of,
                next_same_family_event_opens_at=latest_next_open,
                reissue_exposure=latest_exposure,
            )
            current_absorption_exit = measure_supply_absorption_exit(
                item_id=spec.item_id,
                event=latest_event,
                historical_buckets=buckets,
                as_of=spec.as_of,
                next_same_family_event_opens_at=latest_next_open,
            )

        for event_key, recovery in recovery_by_key.items():
            event = event_by_key.get(event_key)
            if event is None:
                continue
            next_open = min(
                (
                    value.opens_at
                    for value in events
                    if value.family == event.family and value.opens_at > event.opens_at
                ),
                default=None,
            )
            close_entry = first_post_event_trade_proxy(
                event=event, historical_buckets=buckets, as_of=spec.as_of
            )
            close_entry_price = close_entry[1] if close_entry is not None else None
            for checkpoint in recovery.checkpoints:
                if not checkpoint.available or checkpoint.observed_at is None:
                    continue
                snapshot_exposure = derive_reissue_exposure_features(
                    item_id=spec.item_id,
                    as_of=checkpoint.observed_at,
                    events=reissue_events,
                )
                snapshot = measure_scarcity_rebuild(
                    item_id=spec.item_id,
                    event=event,
                    historical_buckets=buckets,
                    as_of=checkpoint.observed_at,
                    next_same_family_event_opens_at=next_open,
                    reissue_exposure=snapshot_exposure,
                )
                absorption = measure_supply_absorption_exit(
                    item_id=spec.item_id,
                    event=event,
                    historical_buckets=buckets,
                    as_of=checkpoint.observed_at,
                    next_same_family_event_opens_at=next_open,
                )
                forward = evaluate_scarcity_rebuild_forward_outcome(
                    snapshot=snapshot,
                    historical_buckets=buckets,
                    evaluation_as_of=spec.as_of,
                    forward_horizon_days=60,
                    next_same_family_event_opens_at=next_open,
                )
                from_close = _rebase_forward_outcome(forward, close_entry_price)
                scarcity_examples.append(
                    {
                        "item_id": spec.item_id,
                        "item_name": spec.item_name,
                        "role": spec.role,
                        "event_key": event.event_key,
                        "family": event.family,
                        "checkpoint_horizon_days": checkpoint.horizon_days,
                        "snapshot": asdict(snapshot),
                        "absorption_exit": asdict(absorption),
                        "forward_60d_outcome": asdict(forward),
                        "forward_60d_outcome_from_close": from_close,
                    }
                )

        item_report.update(
            {
                "event_impacts": [asdict(value) for value in impacts.impacts],
                "impact_profile": asdict(impacts.profile),
                "recovery_profile": asdict(recoveries.profile),
                "current_scarcity_rebuild": (
                    asdict(current_scarcity_rebuild)
                    if current_scarcity_rebuild is not None
                    else None
                ),
                "current_absorption_exit": (
                    asdict(current_absorption_exit)
                    if current_absorption_exit is not None
                    else None
                ),
                "walk_forward_example_count": sum(
                    value.item_id == spec.item_id for value in built
                ),
                "scarcity_rebuild_example_count": sum(
                    value["item_id"] == spec.item_id for value in scarcity_examples
                ),
                "explanation_codes": ["named_history_bound_to_official_supply_catalog"],
            }
        )
        item_reports.append(item_report)

    prior_evidence: list[HistoricalReturnOutcomeEvidence] = []
    for example in built:
        if example.role != "calibration":
            continue
        evidence = return_outcome_evidence(
            item_id=example.item_id,
            estimate=example.estimate,
            outcome=example.outcome,
        )
        if evidence is not None:
            prior_evidence.append(evidence)

    decisions: list[dict[str, Any]] = []
    resolved_item_local: list[ResolvedRatioForecast] = []
    resolved_family: list[ResolvedRatioForecast] = []
    for example in built:
        family_prior = estimate_family_recovery_prior(
            target_item_id=example.item_id,
            target_event_key=example.event_key,
            family=example.family,
            prediction_as_of=example.estimate.prediction_as_of,
            horizon_days=example.horizon_days,
            entry_proxy_price=example.estimate.entry_proxy_price,
            prior_outcomes=prior_evidence,
        )
        decision = corroborate_post_rerelease_signal(
            primary=example.estimate,
            family_prior=family_prior,
        )
        fallback = evaluate_sparse_baseline_family_fallback(
            primary=example.estimate,
            family_prior=family_prior,
        )
        audit = audit_family_prior_generalization(
            target_item_id=example.item_id,
            target_event_key=example.event_key,
            family=example.family,
            prediction_as_of=example.estimate.prediction_as_of,
            horizon_days=example.horizon_days,
            entry_proxy_price=example.estimate.entry_proxy_price,
            prior_outcomes=prior_evidence,
        )
        generalization = classify_generalization_aware_decision(
            hierarchical=decision,
            sparse_fallback=fallback,
            audit=audit,
        )
        item_local_forecast = forecast_price_distribution(
            item_id=example.item_id,
            event_key=example.event_key,
            family=example.family,
            prediction_as_of=example.estimate.prediction_as_of,
            horizon_days=example.horizon_days,
            basis="PRE_EVENT_MEDIAN",
            anchor_price=example.estimate.current_pre_price_median,
            entry_proxy_price=example.estimate.entry_proxy_price,
            observations=example.item_local_ratio_observations,
        )
        family_observations = family_ratio_observations(
            family=example.family,
            horizon_days=example.horizon_days,
            prediction_as_of=example.estimate.prediction_as_of,
            exclude_item_id=example.item_id,
            prior_outcomes=prior_evidence,
        )
        family_forecast = forecast_price_distribution(
            item_id=example.item_id,
            event_key=example.event_key,
            family=example.family,
            prediction_as_of=example.estimate.prediction_as_of,
            horizon_days=example.horizon_days,
            basis="ENTRY_PROXY",
            anchor_price=example.estimate.entry_proxy_price,
            entry_proxy_price=example.estimate.entry_proxy_price,
            observations=family_observations,
        )
        item_local_score = _score_distribution(item_local_forecast, example.outcome)
        family_score = _score_distribution(family_forecast, example.outcome)
        _collect_resolution(
            resolved_item_local, item_local_forecast, item_local_score
        )
        _collect_resolution(resolved_family, family_forecast, family_score)
        prediction_exposure = derive_reissue_exposure_features(
            item_id=example.item_id,
            as_of=example.estimate.prediction_as_of,
            events=reissue_events_by_item[example.item_id],
        )
        decisions.append(
            {
                "item_id": example.item_id,
                "item_name": example.item_name,
                "role": example.role,
                "event_key": example.event_key,
                "family": example.family,
                "horizon_days": example.horizon_days,
                "reissue_exposure_at_prediction": asdict(prediction_exposure),
                "primary": asdict(example.estimate),
                "family_prior": asdict(family_prior),
                "hierarchical": asdict(decision),
                "sparse_baseline_fallback": asdict(fallback),
                "family_prior_generalization": asdict(audit),
                "price_distribution_item_local": asdict(item_local_forecast),
                "price_distribution_family": asdict(family_forecast),
                "price_distribution_outcome_item_local": (
                    asdict(item_local_score) if item_local_score is not None else None
                ),
                "price_distribution_outcome_family": (
                    asdict(family_score) if family_score is not None else None
                ),
                "generalization_aware": asdict(generalization),
                "outcome": asdict(example.outcome),
            }
        )

    metrics = _metrics(decisions)
    metrics["scarcity_rebuild"] = _scarcity_rebuild_metrics(scarcity_examples)
    metrics["absorption_exit"] = _absorption_exit_metrics(scarcity_examples)
    metrics["price_distribution"] = _price_distribution_metrics(decisions)
    metrics["absorption_policy_walk_forward"] = _absorption_policy_walk_forward(
        scarcity_examples
    )
    metrics["rerelease_bottom_fishing"] = _bottom_fishing_metrics(bottom_fishing)
    metrics["walk_forward_calibration"] = _walk_forward_metrics(
        item_local=tuple(resolved_item_local), family=tuple(resolved_family)
    )
    metrics["negative_control_item_count"] = sum(
        value["role"] == "negative_control" for value in item_reports
    )
    metrics["unmatched_negative_control_item_count"] = sum(
        value["role"] == "negative_control"
        and value["catalog_match_status"] == "UNMATCHED"
        for value in item_reports
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "catalog_version": DEFAULT_SUPPLY_POOL_CATALOG.version,
        "methodology": {
            "primary_model": "item-specific prior-return recovery",
            "secondary_model": "same-family independent cross-item observed outcome ratio prior",
            "family_prior_target_item_policy": "exclude all historical outcomes from target_item_id",
            "hierarchy": "family prior may confirm/veto but never promote a non-positive primary",
            "family_prior_minimum_examples": 2,
            "family_prior_minimum_distinct_items": 2,
            "family_profitability_support_rule": "strictly greater than 0.5",
            "generalization_audit": (
                "leave-one-item-out and leave-one-event-out; family evidence is "
                "generalization-ready only when every fold remains available and positive"
            ),
            "generalization_minimum_item_breadth": 3,
            "generalization_minimum_event_breadth": 2,
            "fee_rate": "0.15",
            "price_proxy_semantics": "historical trade VWAP proxy; not executable quote or fill",
            "reissue_exposure_semantics": (
                "descriptive point-in-time counts; tradeable coupon supply and "
                "account-only demand substitution are separate and never converted "
                "into a price multiplier or automatic penalty"
            ),
            "scarcity_rebuild_semantics": (
                "research-only two-axis state: price rebuild is normalized from the "
                "active-event trough toward the item-local pre-event median; activity "
                "absorption is normalized from active-event activity toward the pre-event "
                "activity median; the weaker clipped axis is rebuild_progress_floor"
            ),
            "scarcity_rebuild_forward_label": (
                "60-day historical trade VWAP proxy after 15% fee; evaluation-only, "
                "never used to construct the point-in-time scarcity snapshot"
            ),
            "absorption_exit_semantics": (
                "stopping-condition regime test: absorption is declared over only when "
                "the activity spike has decayed toward the pre-event baseline and the "
                "post-event price is neither making fresh lows nor still trending down, "
                "each held across a dwell window of consecutive observed UTC days; it "
                "measures regime, never recovery level and never expected return"
            ),
            "price_distribution_semantics": (
                "weighted empirical predictive distribution over already observed "
                "recovery ratios; quantiles are read off the inverse CDF without "
                "interpolation and a quantile level is marked supported only inside "
                "[1/(ESS+1), ESS/(ESS+1)]"
            ),
            "price_distribution_scoring": (
                "mid probability integral transform for calibration, Brier score for "
                "the fee-adjusted profit probability, and pinball loss on net ROI for "
                "the supported quantiles; the binary research signal is scored on the "
                "same outcomes as the comparison baseline"
            ),
            "absorption_exit_policy_comparison": (
                "LEGACY_ONLY reproduces the fixed one-event-duration withholding window; "
                "EVIDENCE_EXTENDS_LEGACY can only withhold longer; "
                "EVIDENCE_REPLACES_LEGACY lets confirmed evidence also release inside "
                "the legacy window"
            ),
            "network_access": False,
        },
        "items": item_reports,
        "decisions": decisions,
        "scarcity_rebuild_examples": scarcity_examples,
        "metrics": metrics,
    }


_ABSORPTION_CANDIDATE_GROUPS = ("ABSORPTION_ACTIVE",)
# The state that means "we could not tell" outperformed every real state at
# every checkpoint on this cohort, off 26 rows and two vehicles. Leaving it in
# the evidence lets a data-quality artifact decide a trading policy, so the
# selection is run with and without it.
_ABSORPTION_UNMEASURABLE_GROUPS = ("INSUFFICIENT_EVIDENCE",)


def _absorption_policy_walk_forward(
    examples: list[dict[str, Any]]
) -> dict[str, Any]:
    """Replay the absorption withholding decision strictly point-in-time.

    Two entry conventions are reported and they answer different questions.
    ``checkpoint_entry`` is the one a decision taken at a checkpoint could
    actually act on, because the close price is already in the past by then.
    ``post_close_entry`` is counterfactual at that instant and is here only to
    say whether the state label carries information at all, separately from
    whether acting on it at a checkpoint is possible.
    """

    result: dict[str, Any] = {
        "note": (
            "selection is fitted only on rows whose outcomes had closed at "
            "each decision instant, stratified by checkpoint horizon so the "
            "choice cannot rediscover that later checkpoints return more. "
            "No matched comparison exists: withholding changes only which "
            "rows are traded, so on the rows it publishes the baseline is "
            "identical to it by construction."
        ),
        "candidate_groups": list(_ABSORPTION_CANDIDATE_GROUPS),
        "actionable_convention": "checkpoint_entry",
    }
    for convention, outcome_key in (
        ("checkpoint_entry", "forward_60d_outcome"),
        ("post_close_entry", "forward_60d_outcome_from_close"),
    ):
        rows = _absorption_policy_rows(examples, outcome_key, drop_unmeasurable=False)
        rows_measured = _absorption_policy_rows(
            examples, outcome_key, drop_unmeasurable=True
        )
        result[convention] = {
            "all_states": _policy_walk_forward_block(rows),
            "excluding_unmeasurable_state": _policy_walk_forward_block(rows_measured),
        }
    return result


def _absorption_policy_rows(
    examples: list[dict[str, Any]],
    outcome_key: str,
    *,
    drop_unmeasurable: bool,
) -> list[PolicyDecisionRow]:
    rows: list[PolicyDecisionRow] = []
    for value in examples:
        outcome = value[outcome_key]
        if not outcome["outcome_available"] or outcome["net_roi"] is None:
            continue
        state = value["absorption_exit"]["state"]
        if drop_unmeasurable and state in _ABSORPTION_UNMEASURABLE_GROUPS:
            continue
        closed_at = value["forward_60d_outcome"]["observed_at"]
        if closed_at is None:
            continue
        roi = Decimal(outcome["net_roi"])
        rows.append(
            PolicyDecisionRow(
                item_id=value["item_id"],
                row_key=f"{value['event_key']}@{value['checkpoint_horizon_days']}",
                group=state,
                stratum=f"checkpoint_{value['checkpoint_horizon_days']}d",
                decided_at=_parse_utc(value["snapshot"]["as_of"]),
                outcome_closed_at=_parse_utc(closed_at),
                net_roi=roi,
                profitable=roi > Decimal("0"),
            )
        )
    return rows


def _policy_walk_forward_block(rows: list[PolicyDecisionRow]) -> dict[str, Any]:
    baseline = evaluate_policy_walk_forward(
        rows=rows, candidate_groups=_ABSORPTION_CANDIDATE_GROUPS
    )
    applied = evaluate_policy_walk_forward(
        rows=rows,
        candidate_groups=_ABSORPTION_CANDIDATE_GROUPS,
        policy="APPLY",
    )
    relaxed = evaluate_policy_walk_forward(
        rows=rows,
        candidate_groups=_ABSORPTION_CANDIDATE_GROUPS,
        config=PolicyWalkForwardConfig(
            minimum_closed_rows=20,
            minimum_distinct_items=3,
            minimum_stratum_rows=3,
            minimum_qualifying_strata=2,
        ),
        policy="APPLY",
    )
    return {
        "row_count": len(rows),
        "never_withhold": _policy_arms(baseline),
        "shipped_defaults": _policy_arms(applied),
        "relaxed_thresholds": _policy_arms(relaxed),
    }


def _policy_arms(report: Any) -> dict[str, Any]:
    return {
        "trial_count": report.trial_count,
        "withholding_trial_count": report.withholding_trial_count,
        "status_counts": dict(report.status_counts),
        "baseline": _policy_arm(report.baseline),
        "walk_forward": _policy_arm(report.walk_forward),
        "by_stratum": [
            {
                "stratum": stratum,
                "baseline": _policy_arm(base),
                "walk_forward": _policy_arm(walk),
            }
            for stratum, base, walk in report.by_stratum
        ],
        "explanation_codes": list(report.explanation_codes),
    }


def _policy_arm(arm: Any) -> dict[str, Any]:
    return {
        "published_count": arm.published_count,
        "withheld_count": arm.withheld_count,
        "profitable_count": arm.profitable_count,
        "support_share": arm.support_share,
        "median_net_roi": arm.median_net_roi,
    }


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _bottom_fishing_metrics(
    trials_by_hold: dict[int, list[BottomFishingTrial]]
) -> dict[str, Any]:
    """Compare entry timing rules against the fixed entry the stack uses today.

    ``TROUGH_ORACLE`` appears in the table as an upper bound and is marked
    unattainable. Reading it as a result would be reading a number that assumes
    the buyer knew the future, and its only purpose is to say whether enough
    room exists for any causal rule to be worth having.
    """

    result: dict[str, Any] = {
        "note": (
            "IMMEDIATE is the entry the opportunity stack uses today: the "
            "first completed UTC day after the event closes. Every rule holds "
            "for the same number of days from its own entry, so a later entry "
            "is never charged for a shorter hold. TROUGH_ORACLE is an "
            "unattainable bound, not a strategy."
        ),
        "causal_rules": list(CAUSAL_ENTRY_RULES),
    }
    for holding_days, trials in sorted(trials_by_hold.items()):
        comparison = compare_bottom_fishing_rules(trials=trials)
        result[f"hold_{holding_days}d"] = {
            "event_count": comparison.event_count,
            "summaries": [
                {
                    "rule": value.rule,
                    "attainable": value.attainable,
                    "trial_count": value.trial_count,
                    "entered_count": value.entered_count,
                    "not_triggered_count": value.not_triggered_count,
                    "unavailable_count": value.unavailable_count,
                    "observed_count": value.observed_count,
                    "trigger_share": value.trigger_share,
                    "mean_days_waited": value.mean_days_waited,
                    "median_entry_discount": value.median_entry_discount,
                    "profitable_count": value.profitable_count,
                    "support_share": value.support_share,
                    "median_net_roi": value.median_net_roi,
                }
                for value in comparison.summaries
            ],
            "matched_against_immediate": [
                {
                    "rule": value.rule,
                    "attainable": value.attainable,
                    "matched_count": value.matched_count,
                    "rule_profitable_count": value.rule_profitable_count,
                    "rule_support_share": value.rule_support_share,
                    "rule_median_net_roi": value.rule_median_net_roi,
                    "benchmark_profitable_count": value.benchmark_profitable_count,
                    "benchmark_support_share": value.benchmark_support_share,
                    "benchmark_median_net_roi": value.benchmark_median_net_roi,
                    "median_net_roi_difference": value.median_net_roi_difference,
                }
                for value in comparison.matched
            ],
            "explanation_codes": list(comparison.explanation_codes),
        }
    return result


def _collect_resolution(
    sink: list[ResolvedRatioForecast], forecast: Any, score: Any
) -> None:
    """Record a forecast whose label has closed, for walk-forward calibration.

    Only a scored forecast qualifies: an unscored one either was never
    available or its outcome had not become observable, and in both cases there
    is no label to calibrate against.
    """

    if score is None:
        return
    sink.append(
        ResolvedRatioForecast(
            item_id=forecast.item_id,
            event_key=forecast.event_key,
            horizon_days=forecast.horizon_days,
            basis=forecast.basis,
            prediction_as_of=forecast.prediction_as_of,
            outcome_observed_at=score.outcome_observed_at,
            realized_ratio=score.realized_ratio,
            support=forecast.support,
        )
    )


def _walk_forward_metrics(
    *,
    item_local: tuple[ResolvedRatioForecast, ...],
    family: tuple[ResolvedRatioForecast, ...],
) -> dict[str, Any]:
    """Replay dispersion calibration with no row contributing to its own fit.

    Two configurations are reported. ``shipped_defaults`` is what production
    would do, and on a cohort this size it refuses on every row, which is the
    honest result rather than a missing one. ``relaxed_thresholds`` lowers the
    sample floors purely to show the direction the correction moves in; its
    numbers are a direction, not a licence to switch the policy on.
    """

    relaxed = WalkForwardCalibrationConfig(
        minimum_resolved_forecasts=3, minimum_distinct_items=2
    )
    return {
        "note": (
            "every factor is fitted only on labels that had closed at that "
            "row's own decision instant; a row never contributes to the fit "
            "that scores it"
        ),
        "item_local": {
            "resolved_forecast_count": len(item_local),
            "shipped_defaults": _walk_forward_block(item_local, None),
            "relaxed_thresholds": _walk_forward_block(item_local, relaxed),
        },
        "family_cross_item": {
            "resolved_forecast_count": len(family),
            "shipped_defaults": _walk_forward_block(family, None),
            "relaxed_thresholds": _walk_forward_block(family, relaxed),
        },
    }


def _walk_forward_block(
    resolutions: tuple[ResolvedRatioForecast, ...],
    config: WalkForwardCalibrationConfig | None,
) -> dict[str, Any]:
    report = evaluate_walk_forward_calibration(
        resolutions=resolutions, config=config
    )
    return {
        "trial_count": report.trial_count,
        "calibrated_trial_count": report.calibrated_trial_count,
        "status_counts": dict(report.status_counts),
        "raw_uniformity_criterion": report.raw_uniformity_criterion,
        "calibrated_uniformity_criterion": report.calibrated_uniformity_criterion,
        "mean_applied_factor": report.mean_applied_factor,
        "interval_coverage": [
            {
                "nominal_coverage": value.nominal_coverage,
                "trial_count": value.trial_count,
                "raw_covered_count": value.raw_covered_count,
                "calibrated_covered_count": value.calibrated_covered_count,
            }
            for value in report.coverage
        ],
        "support_reach": {
            "support_atom_counts": {
                str(size): count for size, count in report.support_reach.support_atom_counts
            },
            "raw_below_count": report.support_reach.raw_below_count,
            "raw_inside_count": report.support_reach.raw_inside_count,
            "raw_above_count": report.support_reach.raw_above_count,
            "calibrated_below_count": report.support_reach.calibrated_below_count,
            "calibrated_inside_count": report.support_reach.calibrated_inside_count,
            "calibrated_above_count": report.support_reach.calibrated_above_count,
        },
        "location_bias": {
            "median_transform": report.location_bias.median_transform,
            "below_centre_count": report.location_bias.below_centre_count,
            "at_centre_count": report.location_bias.at_centre_count,
            "above_centre_count": report.location_bias.above_centre_count,
        },
        "explanation_codes": list(report.explanation_codes),
    }


def _score_distribution(forecast: Any, outcome: Any) -> Any:
    """Score a forecast only when its outcome had actually become observable."""

    if not forecast.available:
        return None
    if not outcome.target_available or outcome.exit_proxy_price is None:
        return None
    if outcome.observed_at is None or outcome.observed_at <= forecast.prediction_as_of:
        return None
    return evaluate_price_distribution_outcome(
        forecast=forecast,
        realized_exit_price=outcome.exit_proxy_price,
        outcome_observed_at=outcome.observed_at,
    )


def _price_distribution_metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the predictive distribution against the binary research signal.

    Both are scored with the Brier score on exactly the same rows, so a lower
    distribution Brier is a like-for-like improvement rather than a different
    question being answered. Rows repeat item/event/horizon combinations and are
    not independent.
    """

    sources = {
        "item_local": (
            "price_distribution_item_local",
            "price_distribution_outcome_item_local",
        ),
        "family_cross_item": (
            "price_distribution_family",
            "price_distribution_outcome_family",
        ),
    }
    result: dict[str, Any] = {
        "decision_count": len(decisions),
        "interpretation": (
            "descriptive only; a lower Brier or pinball loss on this cohort is a "
            "calibration candidate, not an accepted production change"
        ),
    }
    for name, (forecast_key, outcome_key) in sources.items():
        status_counts: dict[str, int] = {}
        for value in decisions:
            status = value[forecast_key]["status"]
            status_counts[status] = status_counts.get(status, 0) + 1
        scored = [value for value in decisions if value[outcome_key] is not None]
        distribution_brier = [
            value[outcome_key]["brier_score"]
            for value in scored
            if value[outcome_key]["brier_score"] is not None
        ]
        signal_brier = [
            (
                Decimal("1") if value["primary"]["research_signal_positive"] else Decimal("0")
            )
            - (Decimal("1") if value[outcome_key]["realized_profitable"] else Decimal("0"))
            for value in scored
        ]
        pinball = [
            value[outcome_key]["mean_supported_pinball_loss"]
            for value in scored
            if value[outcome_key]["mean_supported_pinball_loss"] is not None
        ]
        pit = [
            value[outcome_key]["probability_integral_transform"] for value in scored
        ]
        coverage: dict[str, dict[str, int]] = {}
        for value in scored:
            for interval in value[outcome_key]["interval_coverages"]:
                if not interval["supported"]:
                    continue
                row = coverage.setdefault(
                    format(interval["nominal_coverage"], "f"),
                    {"supported_count": 0, "covered_count": 0},
                )
                row["supported_count"] += 1
                row["covered_count"] += 1 if interval["covered"] else 0
        result[name] = {
            "status_counts": status_counts,
            "scored_count": len(scored),
            "mean_distribution_brier": _decimal_mean(tuple(distribution_brier)),
            "mean_binary_signal_brier": _decimal_mean(
                tuple(value * value for value in signal_brier)
            ),
            "mean_supported_pinball_loss": _decimal_mean(tuple(pinball)),
            "probability_integral_transform_histogram": _pit_histogram(tuple(pit)),
            "supported_interval_coverage": {
                key: {
                    **row,
                    "empirical_coverage": (
                        Decimal(row["covered_count"]) / Decimal(row["supported_count"])
                        if row["supported_count"]
                        else None
                    ),
                }
                for key, row in sorted(coverage.items())
            },
        }
    return result


def _pit_histogram(values: tuple[Decimal, ...], bins: int = 5) -> dict[str, int]:
    edges = [Decimal(index) / Decimal(bins) for index in range(bins + 1)]
    histogram = {
        f"{format(edges[index], 'f')}-{format(edges[index + 1], 'f')}": 0
        for index in range(bins)
    }
    for value in values:
        index = min(bins - 1, int(value * Decimal(bins)))
        key = f"{format(edges[index], 'f')}-{format(edges[index + 1], 'f')}"
        histogram[key] += 1
    return histogram


def _decimal_mean(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _absorption_exit_metrics(examples: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize absorption-exit states and what each withholding policy would do.

    Rows repeat item/event/horizon combinations, so every share below is
    descriptive support, not an independent estimate.
    """

    by_state: dict[str, list[dict[str, Any]]] = {}
    legacy_agreement = {
        "legacy_active_evidence_active": 0,
        "legacy_active_evidence_exited": 0,
        "legacy_expired_evidence_active": 0,
        "legacy_expired_evidence_exited": 0,
        "evidence_undetermined": 0,
    }
    for value in examples:
        snapshot = value["absorption_exit"]
        by_state.setdefault(snapshot["state"], []).append(value)
        evidence_active = _evidence_absorption_active(snapshot["state"])
        if evidence_active is None:
            legacy_agreement["evidence_undetermined"] += 1
        else:
            legacy_side = (
                "legacy_active"
                if snapshot["legacy_absorption_active"]
                else "legacy_expired"
            )
            evidence_side = "evidence_active" if evidence_active else "evidence_exited"
            legacy_agreement[f"{legacy_side}_{evidence_side}"] += 1

    state_rows = [
        {
            "state": state,
            "example_count": len(by_state[state]),
            **_forward_outcome_summary(by_state[state]),
        }
        for state in sorted(by_state)
    ]
    policy_rows = []
    for policy in ABSORPTION_WITHHOLD_POLICIES:
        published = [
            value
            for value in examples
            if not _policy_withhold(policy, value["absorption_exit"])
        ]
        policy_rows.append(
            {
                "policy": policy,
                "withheld_example_count": len(examples) - len(published),
                "published_example_count": len(published),
                **_forward_outcome_summary(published),
            }
        )
    return {
        "example_count": len(examples),
        "state_metrics": state_rows,
        "legacy_agreement": legacy_agreement,
        "policy_metrics": policy_rows,
        "interpretation": (
            "descriptive only; a policy that publishes fewer but better checkpoints is "
            "a candidate, not an accepted production change"
        ),
    }


def _evidence_absorption_active(state: str) -> bool | None:
    if state == "ABSORPTION_ACTIVE":
        return True
    if state in {"ABSORPTION_EXIT_CONFIRMED", "NO_CLEAR_SUPPLY_SHOCK"}:
        return False
    return None


def _policy_withhold(policy: str, snapshot: dict[str, Any]) -> bool:
    legacy_active = bool(snapshot["legacy_absorption_active"])
    evidence_active = _evidence_absorption_active(snapshot["state"])
    if policy == "LEGACY_ONLY":
        return legacy_active
    if policy == "EVIDENCE_EXTENDS_LEGACY":
        return legacy_active or evidence_active is True
    if evidence_active is None:
        return legacy_active
    return evidence_active


def _rebase_forward_outcome(
    forward: Any, entry_price: Decimal | None
) -> dict[str, Any]:
    """Re-price one forward outcome from the post-close entry instead.

    Only the entry changes. The exit day, its price, the fee and the proceeds
    are the ones the outcome already computed, so this is arithmetic on a
    finished result rather than a second implementation of exit selection that
    could drift away from the first.
    """

    proceeds = forward.sale_proceeds
    available = (
        forward.outcome_available
        and proceeds is not None
        and entry_price is not None
        and entry_price > Decimal("0")
    )
    if not available:
        return {
            "outcome_available": False,
            "entry_proxy_price": entry_price,
            "exit_proxy_price": forward.exit_proxy_price,
            "sale_proceeds": proceeds,
            "net_profit": None,
            "net_roi": None,
            "explanation_codes": [
                "forward_outcome_from_close_entry_unavailable",
            ],
        }
    assert proceeds is not None and entry_price is not None
    profit = proceeds - entry_price
    return {
        "outcome_available": True,
        "entry_proxy_price": entry_price,
        "exit_proxy_price": forward.exit_proxy_price,
        "sale_proceeds": proceeds,
        "net_profit": profit,
        "net_roi": profit / entry_price,
        "explanation_codes": [
            "forward_outcome_repriced_from_first_post_close_day",
            "same_exit_day_and_proceeds_as_the_checkpoint_entry_outcome",
        ],
    }


def _progress_pairs(
    examples: list[dict[str, Any]], outcome_key: str
) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple(
        (value["snapshot"]["rebuild_progress_floor"], value[outcome_key]["net_roi"])
        for value in examples
        if value["snapshot"]["rebuild_progress_floor"] is not None
        and value[outcome_key]["net_roi"] is not None
    )


def _forward_outcome_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    """Score each state under both entry conventions on the same exits.

    ``forward_*`` buys at the checkpoint, which is what every state table has
    always measured. ``close_entry_*`` buys the first completed day after the
    event closed and sells on exactly the same day for exactly the same
    proceeds. Reporting both is the only way to see whether a state label
    discriminates under the entry that makes money, or only under the one that
    does not.
    """

    rois = tuple(
        value["forward_60d_outcome"]["net_roi"]
        for value in values
        if value["forward_60d_outcome"]["outcome_available"]
        and value["forward_60d_outcome"]["net_roi"] is not None
    )
    close_rois = tuple(
        value["forward_60d_outcome_from_close"]["net_roi"]
        for value in values
        if value["forward_60d_outcome_from_close"]["outcome_available"]
        and value["forward_60d_outcome_from_close"]["net_roi"] is not None
    )
    profitable = sum(value > Decimal("0") for value in rois)
    close_profitable = sum(value > Decimal("0") for value in close_rois)
    return {
        "forward_outcome_count": len(rois),
        "forward_profitable_after_fee_count": profitable,
        "forward_profitability_support_share": (
            Decimal(profitable) / Decimal(len(rois)) if rois else None
        ),
        "median_forward_net_roi": _decimal_median(rois),
        "close_entry_outcome_count": len(close_rois),
        "close_entry_profitable_after_fee_count": close_profitable,
        "close_entry_support_share": (
            Decimal(close_profitable) / Decimal(len(close_rois))
            if close_rois
            else None
        ),
        "close_entry_median_net_roi": _decimal_median(close_rois),
    }


def _scarcity_rebuild_metrics(examples: list[dict[str, Any]]) -> dict[str, Any]:
    by_state: dict[str, list[dict[str, Any]]] = {}
    current_progress: list[Decimal] = []
    for value in examples:
        snapshot = value["snapshot"]
        state = snapshot["state"]
        by_state.setdefault(state, []).append(value)
        progress = snapshot["rebuild_progress_floor"]
        if progress is not None:
            current_progress.append(progress)

    state_rows: list[dict[str, Any]] = []
    for state in sorted(by_state):
        values = by_state[state]
        state_rows.append(
            {
                "state": state,
                "example_count": len(values),
                **_forward_outcome_summary(values),
            }
        )
    association_pairs = _progress_pairs(examples, "forward_60d_outcome")
    # Stage 10 read this correlation off the checkpoint entry alone. It is the
    # claim stage 11 was designed around, so it is also measured under the
    # entry that actually makes money rather than only under the one that does
    # not.
    close_association_pairs = _progress_pairs(
        examples, "forward_60d_outcome_from_close"
    )
    return {
        "example_count": len(examples),
        "forward_outcome_count": sum(
            value["forward_60d_outcome"]["outcome_available"] for value in examples
        ),
        "progress_forward_roi_pair_count": len(association_pairs),
        "progress_forward_roi_spearman": _spearman_decimal(association_pairs),
        "close_entry_progress_forward_roi_pair_count": len(close_association_pairs),
        "close_entry_progress_forward_roi_spearman": _spearman_decimal(
            close_association_pairs
        ),
        "state_metrics": state_rows,
        "interpretation": (
            "descriptive only; repeated item/event/horizon rows are not independent and "
            "scarcity rebuild state is not a buy signal"
        ),
    }


def _spearman_decimal(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> Decimal | None:
    if len(pairs) < 2:
        return None
    x_ranks = _average_ranks(tuple(value[0] for value in pairs))
    y_ranks = _average_ranks(tuple(value[1] for value in pairs))
    x_mean = sum(x_ranks, Decimal("0")) / Decimal(len(x_ranks))
    y_mean = sum(y_ranks, Decimal("0")) / Decimal(len(y_ranks))
    numerator = sum(
        ((x - x_mean) * (y - y_mean) for x, y in zip(x_ranks, y_ranks, strict=True)),
        Decimal("0"),
    )
    x_ss = sum(((value - x_mean) ** 2 for value in x_ranks), Decimal("0"))
    y_ss = sum(((value - y_mean) ** 2 for value in y_ranks), Decimal("0"))
    if x_ss == Decimal("0") or y_ss == Decimal("0"):
        return None
    # Decimal has no cross-version issue for sqrt of positive finite values.
    return numerator / (x_ss * y_ss).sqrt()


def _average_ranks(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [Decimal("0")] * len(values)
    offset = 0
    while offset < len(ordered):
        end = offset
        while end + 1 < len(ordered) and values[ordered[end + 1]] == values[ordered[offset]]:
            end += 1
        average = (Decimal(offset + 1) + Decimal(end + 1)) / Decimal("2")
        for position in range(offset, end + 1):
            ranks[ordered[position]] = average
        offset = end + 1
    return tuple(ranks)


def _decimal_median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _parse_spec(value: object, base: Path) -> ItemSpec:
    if not isinstance(value, dict):
        raise CohortReplayError("manifest item must be an object")
    item_id = value.get("item_id")
    item_name = value.get("item_name")
    history_path = value.get("history_path")
    as_of = value.get("as_of")
    role = value.get("role")
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        raise CohortReplayError("item_id must be a positive integer")
    if not isinstance(item_name, str) or not item_name.strip():
        raise CohortReplayError("item_name must be non-empty")
    if not isinstance(history_path, str) or not history_path.strip():
        raise CohortReplayError("history_path must be non-empty")
    if role not in {"calibration", "diagnostic", "negative_control"}:
        raise CohortReplayError("invalid role")
    return ItemSpec(
        item_id=item_id,
        item_name=item_name,
        history_path=(base / history_path).resolve(),
        as_of=_parse_datetime(as_of),
        role=role,
    )


def _load_history(
    spec: ItemSpec,
) -> tuple[tuple[HistoricalTradeBucketEvidence, ...], dict[str, Any]]:
    payload = json.loads(spec.history_path.read_text(encoding="utf-8"))
    source_schema = "legacy_gaijin_history_response"
    source_item_key = None
    source_url = None
    if payload.get("schema_version") == "gaijin_trade_history_v1":
        response = payload.get("series")
        source_schema = "gaijin_trade_history_v1"
        source_item_key = payload.get("item_key")
        source_url = payload.get("source_url")
        if not isinstance(response, dict):
            raise CohortReplayError(
                f"gaijin_trade_history_v1 series invalid for item {spec.item_id}"
            )
    else:
        response = payload.get("response")
        if not isinstance(response, dict) or response.get("success") is not True:
            raise CohortReplayError(f"history response invalid for item {spec.item_id}")
    buckets: list[HistoricalTradeBucketEvidence] = []
    counts: dict[str, int] = {}
    for granularity, duration in (("1d", timedelta(days=1)), ("1h", timedelta(hours=1))):
        points = response.get(granularity)
        if not isinstance(points, list):
            raise CohortReplayError(f"missing {granularity} points for item {spec.item_id}")
        counts[granularity] = len(points)
        for point in points:
            if not isinstance(point, list) or len(point) != 3:
                raise CohortReplayError("historical trade point must be [timestamp, price_raw, volume]")
            timestamp, price_raw, volume = point
            buckets.append(
                HistoricalTradeBucketEvidence(
                    item_id=spec.item_id,
                    granularity=granularity,  # type: ignore[arg-type]
                    bucket_start_utc=datetime.fromtimestamp(timestamp, UTC),
                    bucket_duration=duration,
                    vwap_price=Decimal(price_raw) / PRICE_SCALE,
                    reported_trade_volume=volume,
                )
            )
    return tuple(buckets), {
        "history_file": spec.history_path.name,
        "history_source_schema": source_schema,
        "source_item_key": source_item_key,
        "source_url": source_url,
        "as_of": spec.as_of,
        "point_count_1d": counts["1d"],
        "point_count_1h": counts["1h"],
        "point_count_total": counts["1d"] + counts["1h"],
    }


def _metrics(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [
        value
        for value in decisions
        if value["role"] == "calibration"
        and value["outcome"]["target_available"]
        and value["outcome"]["realized_net_roi"] is not None
    ]
    primary_confusion = _confusion(evaluated, "primary")
    hierarchical_confusion = _confusion(evaluated, "hierarchical")
    fallback_confusion = _confusion(evaluated, "fallback")
    family_only_counterfactual = _confusion(evaluated, "family")
    generalization_ready_strict = _confusion(evaluated, "generalization_strict")
    generalization_ready_with_sparse = _confusion(evaluated, "generalization_sparse")
    status_counts: dict[str, int] = {}
    for value in decisions:
        status = value["family_prior_generalization"]["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "evaluated_calibration_examples": len(evaluated),
        "primary": primary_confusion,
        "hierarchical": hierarchical_confusion,
        "strict_plus_sparse_review_candidate": fallback_confusion,
        "generalization_ready_strict": generalization_ready_strict,
        "generalization_ready_strict_plus_sparse": generalization_ready_with_sparse,
        "family_only_counterfactual": family_only_counterfactual,
        "generalization_status_counts": status_counts,
        "generalization_ready_decision_count": sum(
            value["family_prior_generalization"]["generalization_ready"]
            for value in decisions
        ),
        "diagnostic_example_count": sum(value["role"] == "diagnostic" for value in decisions),
        "reissue_exposure_outcome_diagnostics": _reissue_exposure_outcome_diagnostics(evaluated),
    }


def _reissue_exposure_outcome_diagnostics(
    values: list[dict[str, Any]],
) -> dict[str, Any]:
    by_tradeable: dict[int, list[Decimal]] = {}
    by_horizon: dict[int, dict[int, list[Decimal]]] = {}
    for value in values:
        exposure = value["reissue_exposure_at_prediction"]
        count = exposure["completed_tradeable_supply_count"]
        roi = value["outcome"]["realized_net_roi"]
        if not isinstance(count, int) or not isinstance(roi, Decimal):
            continue
        by_tradeable.setdefault(count, []).append(roi)
        by_horizon.setdefault(value["horizon_days"], {}).setdefault(count, []).append(roi)

    def summary(points: list[Decimal]) -> dict[str, Any]:
        ordered = sorted(points)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal("2")
        )
        profitable = sum(value > Decimal("0") for value in points)
        return {
            "example_count": len(points),
            "profitable_example_count": profitable,
            "profitable_support_share": Decimal(profitable) / Decimal(len(points)),
            "median_realized_net_roi": median,
        }

    return {
        "interpretation": (
            "descriptive only; repeated horizons/items are not independent and this "
            "table must not be interpreted as a causal reissue penalty"
        ),
        "by_completed_tradeable_supply_count": {
            str(key): summary(points) for key, points in sorted(by_tradeable.items())
        },
        "by_horizon_and_completed_tradeable_supply_count": {
            str(horizon): {
                str(key): summary(points)
                for key, points in sorted(groups.items())
            }
            for horizon, groups in sorted(by_horizon.items())
        },
    }


def _confusion(values: list[dict[str, Any]], key: str) -> dict[str, int]:
    result = {"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0}
    for value in values:
        if key == "primary":
            predicted = bool(value["primary"]["research_signal_positive"])
        elif key == "hierarchical":
            predicted = bool(value["hierarchical"]["research_signal_positive"])
        elif key == "fallback":
            predicted = bool(value["hierarchical"]["research_signal_positive"]) or bool(
                value["sparse_baseline_fallback"]["candidate_positive"]
            )
        elif key == "family":
            predicted = bool(value["family_prior"]["family_support_positive"])
        elif key == "generalization_strict":
            predicted = bool(
                value["generalization_aware"][
                    "generalization_ready_research_signal"
                ]
            )
        elif key == "generalization_sparse":
            predicted = bool(
                value["generalization_aware"][
                    "generalization_ready_research_signal"
                ]
            ) or bool(
                value["generalization_aware"][
                    "generalization_ready_sparse_review_candidate"
                ]
            )
        else:
            raise CohortReplayError(f"unknown confusion mode: {key}")
        realized = value["outcome"]["realized_net_roi"] > Decimal("0")
        if predicted and realized:
            result["true_positive"] += 1
        elif predicted and not realized:
            result["false_positive"] += 1
        elif not predicted and realized:
            result["false_negative"] += 1
        else:
            result["true_negative"] += 1
    return result


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise CohortReplayError("as_of must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CohortReplayError("as_of must be timezone-aware")
    return parsed.astimezone(UTC)


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.name
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


if __name__ == "__main__":
    main()
