"""Strict point-in-time evaluation of a publish-or-withhold policy.

Stage 16 applied this discipline to a fitted parameter. A withholding policy is
the same problem wearing different clothes: the choice of *whether to withhold a
group at all* is itself fitted on outcomes, and fitting it on the whole cohort
and then reporting the result is an in-sample number presented as a finding.
Every selection here is made from rows whose outcomes had already closed at the
decision instant, and no row ever informs the selection that judges it.

Two things are different from stage 16, both learned the hard way on this data.

**Selection is stratified.** Forward return on this cohort rises steadily with
how long ago the event closed, and the state labels are not evenly spread across
that axis, so a pooled comparison partly measures elapsed time. A group is
therefore only withheld when excluding it helps *within* strata, and the
stratum contrast is published so the reader can see where any gain came from.

**There is no matched comparison, because there cannot be one.** A withholding
policy changes only which rows are traded, so on the rows it publishes the
baseline is identical to it by construction; a "matched" table would compare a
set against itself and always tie. What the evidence actually turns on is
whether the withheld group underperforms the group that stays, stratum by
stratum, and that is what ``StratumContrast`` reports with its counts attached.

As everywhere else in this package the default is inert: ``NEVER_WITHHOLD``
publishes exactly what the caller already publishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal


POLICY_WALK_FORWARD_VERSION = "1.0.0"

PolicySelectionStatus = Literal[
    "INSUFFICIENT_CLOSED_EVIDENCE",
    "INSUFFICIENT_DISTINCT_ITEMS",
    "INSUFFICIENT_STRATA",
    "NO_GROUP_QUALIFIED",
    "SELECTED",
]
WithholdPolicy = Literal["NEVER_WITHHOLD", "SHADOW", "APPLY"]
WITHHOLD_POLICIES: tuple[WithholdPolicy, ...] = (
    "NEVER_WITHHOLD",
    "SHADOW",
    "APPLY",
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class PolicyWalkForwardError(ValueError):
    """Raised when a policy walk-forward input is not usable."""


@dataclass(frozen=True, slots=True)
class PolicyDecisionRow:
    """One publishable row whose outcome closes after the decision is made.

    ``decided_at`` is when the publish-or-withhold choice would be taken, and
    ``outcome_closed_at`` is when the result became observable. Keeping them
    apart is the whole point: a row is evidence only after the second instant,
    and a subject only at the first.

    ``stratum`` names the confounder the selection must hold constant. On this
    cohort it is the checkpoint horizon, because forward return grows with it
    and the groups are unevenly spread across it.
    """

    item_id: int
    row_key: str
    group: str
    stratum: str
    decided_at: datetime
    outcome_closed_at: datetime
    net_roi: Decimal
    profitable: bool

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise PolicyWalkForwardError("item_id must be positive.")
        for name in ("row_key", "group", "stratum"):
            if not str(getattr(self, name)).strip():
                raise PolicyWalkForwardError(f"{name} must be non-empty.")
        object.__setattr__(self, "decided_at", _utc(self.decided_at, "decided_at"))
        object.__setattr__(
            self,
            "outcome_closed_at",
            _utc(self.outcome_closed_at, "outcome_closed_at"),
        )
        if self.outcome_closed_at < self.decided_at:
            raise PolicyWalkForwardError(
                "outcome_closed_at must not precede decided_at."
            )
        if not isinstance(self.net_roi, Decimal) or not self.net_roi.is_finite():
            raise PolicyWalkForwardError("net_roi must be a finite Decimal.")
        if not isinstance(self.profitable, bool):
            raise PolicyWalkForwardError("profitable must be a bool.")


@dataclass(frozen=True, slots=True)
class PolicyWalkForwardConfig:
    minimum_closed_rows: int = 40
    minimum_distinct_items: int = 5
    minimum_stratum_rows: int = 6
    minimum_qualifying_strata: int = 3
    embargo_days: int = 0
    minimum_mean_support_gain: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        for name in (
            "minimum_closed_rows",
            "minimum_distinct_items",
            "minimum_stratum_rows",
            "minimum_qualifying_strata",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PolicyWalkForwardError(f"{name} must be a positive integer.")
        if (
            isinstance(self.embargo_days, bool)
            or not isinstance(self.embargo_days, int)
            or self.embargo_days < 0
        ):
            raise PolicyWalkForwardError(
                "embargo_days must be a non-negative integer."
            )
        if (
            not isinstance(self.minimum_mean_support_gain, Decimal)
            or not self.minimum_mean_support_gain.is_finite()
            or self.minimum_mean_support_gain < _ZERO
        ):
            raise PolicyWalkForwardError(
                "minimum_mean_support_gain must be a non-negative Decimal."
            )


@dataclass(frozen=True, slots=True)
class StratumContrast:
    """Within one stratum: how the candidate group fared against the rest.

    Counts are carried beside every share because the shares are computed on a
    handful of non-independent rows, and a reader who cannot see the
    denominators cannot tell a signal from two vehicles.
    """

    stratum: str
    withheld_count: int
    withheld_profitable_count: int
    withheld_support_share: Decimal | None
    retained_count: int
    retained_profitable_count: int
    retained_support_share: Decimal | None
    support_gain: Decimal | None
    qualifying: bool


@dataclass(frozen=True, slots=True)
class GroupEvaluation:
    group: str
    closed_row_count: int
    distinct_item_count: int
    strata: tuple[StratumContrast, ...]
    qualifying_stratum_count: int
    positive_stratum_count: int
    mean_support_gain: Decimal | None
    selected: bool
    explanation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicySelection:
    as_of: datetime
    status: PolicySelectionStatus
    applicable: bool
    withheld_groups: tuple[str, ...]
    closed_row_count: int
    purged_open_outcome_count: int
    purged_embargo_count: int
    distinct_item_count: int
    evaluations: tuple[GroupEvaluation, ...]
    explanation_codes: tuple[str, ...]
    version: str = POLICY_WALK_FORWARD_VERSION


@dataclass(frozen=True, slots=True)
class PolicyTrialOutcome:
    item_id: int
    row_key: str
    group: str
    stratum: str
    decided_at: datetime
    selection_status: PolicySelectionStatus
    evidence_row_count: int
    withheld_groups: tuple[str, ...]
    withheld: bool
    net_roi: Decimal
    profitable: bool


@dataclass(frozen=True, slots=True)
class PolicyArm:
    """One publishing behaviour scored over the whole replay."""

    name: str
    published_count: int
    withheld_count: int
    profitable_count: int
    support_share: Decimal | None
    median_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class PolicyWalkForwardReport:
    trial_count: int
    status_counts: tuple[tuple[PolicySelectionStatus, int], ...]
    withholding_trial_count: int
    baseline: PolicyArm
    walk_forward: PolicyArm
    by_stratum: tuple[tuple[str, PolicyArm, PolicyArm], ...]
    trials: tuple[PolicyTrialOutcome, ...]
    explanation_codes: tuple[str, ...]
    version: str = POLICY_WALK_FORWARD_VERSION


def closed_evidence(
    *,
    rows: Iterable[PolicyDecisionRow],
    as_of: datetime,
    config: PolicyWalkForwardConfig | None = None,
) -> tuple[tuple[PolicyDecisionRow, ...], int, int]:
    """Rows whose outcomes had closed by ``as_of``, and what each rule removed."""

    cfg = config or PolicyWalkForwardConfig()
    moment = _utc(as_of, "as_of")
    cutoff = moment - timedelta(days=cfg.embargo_days)
    usable: list[PolicyDecisionRow] = []
    still_open = 0
    embargoed = 0
    for row in rows:
        if row.outcome_closed_at > moment:
            still_open += 1
            continue
        if row.outcome_closed_at > cutoff:
            embargoed += 1
            continue
        usable.append(row)
    usable.sort(key=lambda value: (value.outcome_closed_at, value.item_id, value.row_key))
    return tuple(usable), still_open, embargoed


def select_withheld_groups(
    *,
    rows: Iterable[PolicyDecisionRow],
    as_of: datetime,
    candidate_groups: Iterable[str],
    config: PolicyWalkForwardConfig | None = None,
) -> PolicySelection:
    """Choose which groups to withhold, from closed evidence only.

    A group qualifies when excluding it raises the support share inside enough
    strata, by enough, and in most of them. Requiring the gain to hold within
    strata is what stops the selection from rediscovering that later checkpoints
    return more, and requiring a majority of strata is what stops one lucky
    cell from carrying the decision.
    """

    cfg = config or PolicyWalkForwardConfig()
    usable, still_open, embargoed = closed_evidence(
        rows=rows, as_of=as_of, config=cfg
    )
    moment = _utc(as_of, "as_of")
    items = {row.item_id for row in usable}
    codes: list[str] = ["policy_walk_forward_point_in_time_evidence_only"]
    if still_open:
        codes.append("policy_walk_forward_purged_rows_with_open_outcomes")
    if embargoed:
        codes.append("policy_walk_forward_purged_embargoed_rows")

    def refuse(status: PolicySelectionStatus, code: str) -> PolicySelection:
        return PolicySelection(
            as_of=moment,
            status=status,
            applicable=False,
            withheld_groups=(),
            closed_row_count=len(usable),
            purged_open_outcome_count=still_open,
            purged_embargo_count=embargoed,
            distinct_item_count=len(items),
            evaluations=(),
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
        )

    if len(usable) < cfg.minimum_closed_rows:
        return refuse(
            "INSUFFICIENT_CLOSED_EVIDENCE",
            "policy_walk_forward_not_enough_closed_rows_to_select",
        )
    if len(items) < cfg.minimum_distinct_items:
        return refuse(
            "INSUFFICIENT_DISTINCT_ITEMS",
            "policy_walk_forward_evidence_not_spread_across_items",
        )

    strata = sorted({row.stratum for row in usable})
    if len(strata) < cfg.minimum_qualifying_strata:
        return refuse(
            "INSUFFICIENT_STRATA",
            "policy_walk_forward_too_few_strata_to_control_elapsed_time",
        )

    evaluations: list[GroupEvaluation] = []
    for group in sorted(dict.fromkeys(candidate_groups)):
        contrasts: list[StratumContrast] = []
        for stratum in strata:
            inside = [
                row
                for row in usable
                if row.stratum == stratum and row.group == group
            ]
            outside = [
                row
                for row in usable
                if row.stratum == stratum and row.group != group
            ]
            qualifying = (
                len(inside) >= cfg.minimum_stratum_rows
                and len(outside) >= cfg.minimum_stratum_rows
            )
            inside_support = _share(inside)
            outside_support = _share(outside)
            contrasts.append(
                StratumContrast(
                    stratum=stratum,
                    withheld_count=len(inside),
                    withheld_profitable_count=sum(row.profitable for row in inside),
                    withheld_support_share=inside_support,
                    retained_count=len(outside),
                    retained_profitable_count=sum(row.profitable for row in outside),
                    retained_support_share=outside_support,
                    support_gain=(
                        outside_support - inside_support
                        if qualifying
                        and inside_support is not None
                        and outside_support is not None
                        else None
                    ),
                    qualifying=qualifying,
                )
            )
        gains = tuple(
            value.support_gain
            for value in contrasts
            if value.qualifying and value.support_gain is not None
        )
        positive = sum(value > _ZERO for value in gains)
        mean_gain = (
            sum(gains, _ZERO) / Decimal(len(gains)) if gains else None
        )
        group_rows = [row for row in usable if row.group == group]
        group_codes: list[str] = []
        selected = False
        if len(gains) < cfg.minimum_qualifying_strata:
            group_codes.append("policy_walk_forward_group_lacks_qualifying_strata")
        elif mean_gain is None or mean_gain < cfg.minimum_mean_support_gain:
            group_codes.append("policy_walk_forward_group_gain_below_threshold")
        elif positive * 2 <= len(gains):
            group_codes.append("policy_walk_forward_group_gain_not_in_most_strata")
        else:
            selected = True
            group_codes.append("policy_walk_forward_group_selected_for_withholding")
        evaluations.append(
            GroupEvaluation(
                group=group,
                closed_row_count=len(group_rows),
                distinct_item_count=len({row.item_id for row in group_rows}),
                strata=tuple(contrasts),
                qualifying_stratum_count=len(gains),
                positive_stratum_count=positive,
                mean_support_gain=mean_gain,
                selected=selected,
                explanation_codes=tuple(dict.fromkeys(group_codes)),
            )
        )

    withheld = tuple(value.group for value in evaluations if value.selected)
    if not withheld:
        codes.append("policy_walk_forward_no_group_met_the_selection_bar")
    return PolicySelection(
        as_of=moment,
        status="SELECTED" if withheld else "NO_GROUP_QUALIFIED",
        applicable=bool(withheld),
        withheld_groups=withheld,
        closed_row_count=len(usable),
        purged_open_outcome_count=still_open,
        purged_embargo_count=embargoed,
        distinct_item_count=len(items),
        evaluations=tuple(evaluations),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def evaluate_policy_walk_forward(
    *,
    rows: Iterable[PolicyDecisionRow],
    candidate_groups: Iterable[str],
    config: PolicyWalkForwardConfig | None = None,
    policy: WithholdPolicy = "NEVER_WITHHOLD",
) -> PolicyWalkForwardReport:
    """Replay the publish decision with selection fitted only on closed rows.

    Under ``NEVER_WITHHOLD`` the walk-forward arm is the baseline by
    construction, which is the inert default. ``SHADOW`` and ``APPLY`` both
    score the selected policy; they differ only in what the caller is expected
    to do with the answer, and both are reported side by side with the
    baseline so the cost of trading fewer rows stays visible.
    """

    cfg = config or PolicyWalkForwardConfig()
    if policy not in WITHHOLD_POLICIES:
        raise PolicyWalkForwardError("unsupported withholding policy.")
    pool = tuple(rows)
    groups = tuple(dict.fromkeys(candidate_groups))
    ordered = sorted(
        pool, key=lambda value: (value.decided_at, value.item_id, value.row_key)
    )
    trials: list[PolicyTrialOutcome] = []
    for row in ordered:
        if policy == "NEVER_WITHHOLD":
            selection = PolicySelection(
                as_of=row.decided_at,
                status="NO_GROUP_QUALIFIED",
                applicable=False,
                withheld_groups=(),
                closed_row_count=0,
                purged_open_outcome_count=0,
                purged_embargo_count=0,
                distinct_item_count=0,
                evaluations=(),
                explanation_codes=("policy_walk_forward_never_withhold",),
            )
        else:
            selection = select_withheld_groups(
                rows=pool,
                as_of=row.decided_at,
                candidate_groups=groups,
                config=cfg,
            )
        trials.append(
            PolicyTrialOutcome(
                item_id=row.item_id,
                row_key=row.row_key,
                group=row.group,
                stratum=row.stratum,
                decided_at=row.decided_at,
                selection_status=selection.status,
                evidence_row_count=selection.closed_row_count,
                withheld_groups=selection.withheld_groups,
                withheld=row.group in selection.withheld_groups,
                net_roi=row.net_roi,
                profitable=row.profitable,
            )
        )

    statuses: dict[PolicySelectionStatus, int] = {}
    for trial in trials:
        statuses[trial.selection_status] = statuses.get(trial.selection_status, 0) + 1
    baseline = _arm("baseline_publish_everything", trials, withheld=False)
    walk = _arm("walk_forward_withholding", trials, withheld=True)
    by_stratum = tuple(
        (
            stratum,
            _arm(
                "baseline_publish_everything",
                [row for row in trials if row.stratum == stratum],
                withheld=False,
            ),
            _arm(
                "walk_forward_withholding",
                [row for row in trials if row.stratum == stratum],
                withheld=True,
            ),
        )
        for stratum in sorted({row.stratum for row in trials})
    )
    codes = [
        "policy_walk_forward_report_research_only",
        "policy_walk_forward_rows_repeat_items_and_are_not_independent",
        (
            "policy_walk_forward_no_matched_comparison_exists_because_"
            "withholding_only_changes_which_rows_are_traded"
        ),
    ]
    withholding_trials = sum(trial.withheld for trial in trials)
    if not trials:
        codes.append("policy_walk_forward_no_rows_to_replay")
    if not withholding_trials:
        codes.append("policy_walk_forward_nothing_was_ever_withheld")
    return PolicyWalkForwardReport(
        trial_count=len(trials),
        status_counts=tuple(sorted(statuses.items())),
        withholding_trial_count=withholding_trials,
        baseline=baseline,
        walk_forward=walk,
        by_stratum=by_stratum,
        trials=tuple(trials),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _arm(
    name: str, trials: list[PolicyTrialOutcome], *, withheld: bool
) -> PolicyArm:
    published = [row for row in trials if not (withheld and row.withheld)]
    rois = tuple(row.net_roi for row in published)
    profitable = sum(row.profitable for row in published)
    return PolicyArm(
        name=name,
        published_count=len(published),
        withheld_count=len(trials) - len(published),
        profitable_count=profitable,
        support_share=(
            Decimal(profitable) / Decimal(len(published)) if published else None
        ),
        median_net_roi=_median(rois),
    )


def _share(rows: list[PolicyDecisionRow]) -> Decimal | None:
    if not rows:
        return None
    return Decimal(sum(row.profitable for row in rows)) / Decimal(len(rows))


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyWalkForwardError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
