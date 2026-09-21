from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from gaijin_market_analytics.cohort_coverage import (
    assess_history_coverage,
    suggest_catalog_vehicle_names,
)
from gaijin_market_analytics.supply_channels import assess_supply_channel_coverage
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG


PLAN_SCHEMA_VERSION = "named_history_batch_plan_v1"
LEDGER_SCHEMA_VERSION = "named_history_batch_ledger_v1"
BINDING_SCHEMA_VERSION = "named_history_binding_v1"
HISTORY_SCHEMA_VERSION = "gaijin_trade_history_v1"
MANIFEST_SCHEMA_VERSION = "named_recovery_cohort_manifest_v1"

# Mirrors of the API import contract, so an unusable capture is rejected here
# rather than after it has been copied to the server inbox.
SUPPORTED_MARKET_ID = "1067"
MAX_POINTS_PER_SERIES = 20_000
MIN_UNIX_SECONDS = 1_500_000_000
MAX_UNIX_SECONDS = 4_102_444_800
MAX_PRICE_RAW = 10_000_000_000_000
MAX_REPORTED_VOLUME = 2_000_000_000
GRANULARITY_SECONDS = {"1h": 3600, "1d": 86400}
CAPTURE_FIELDS = {"schema_version", "item_key", "source_url", "captured_at", "series"}
CATALOG_CSV_FIELDS = ("external_key", "name", "category", "rarity", "is_active")
DEFAULT_INTAKE_HISTORY_DIR = "/var/lib/gaijin-market-web-mvp/intake/history"
DEFAULT_IMPORT_SCRIPT = "./deploy/import-approved-history.sh"
ROLES = ("calibration", "diagnostic", "negative_control")


class BatchImportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CaptureReview:
    """One captured history file, judged before anything is written anywhere."""

    file: str
    verdict: str
    item_key: str | None
    source_url: str | None
    captured_at: datetime | None
    point_count_1d: int
    point_count_1h: int
    bound_vehicle_name: str | None
    binding_role: str | None
    suggested_vehicle_names: tuple[str, ...]
    existing_item_id: int | None
    existing_item_name: str | None
    existing_item_name_binds: bool | None
    catalog_match_status: str | None
    history_start_utc: datetime | None
    history_end_utc: datetime | None
    required_history_start_utc: datetime | None
    covers_required_start: bool | None
    tradeable_event_count: int | None
    fully_usable_event_count: int | None
    supply_channel_coverage: str | None
    supply_channels_reviewed: bool | None
    unmodelled_supply_channels: tuple[str, ...]
    explanation_codes: tuple[str, ...]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Batch preflight and import driver for manually captured Gaijin history "
            "exports. It validates and binds captures offline, emits the exact item "
            "catalog CSV and cohort manifest the existing importers expect, and drives "
            "the audited per-file import script. It never writes to the database "
            "itself and never contacts Gaijin."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="validate, bind and score captures; write nothing")
    _add_common(plan)
    plan.add_argument("--output", type=Path)

    emit = sub.add_parser("emit", help="write the item catalog CSV and/or cohort manifest")
    _add_common(emit)
    emit.add_argument("--items-csv", type=Path)
    emit.add_argument("--manifest", type=Path)

    run = sub.add_parser("import", help="drive the audited per-file import script")
    run.add_argument(
        "--intake-history-dir", type=Path, default=Path(DEFAULT_INTAKE_HISTORY_DIR)
    )
    run.add_argument("--script", type=Path, default=Path(DEFAULT_IMPORT_SCRIPT))
    run.add_argument("--write", action="store_true")
    run.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "plan":
        report = build_plan(
            capture_dir=args.capture_dir,
            binding_path=args.binding,
            existing_items_path=args.existing_items,
            as_of=_as_of(args.as_of),
        )
        _write(report, args.output)
        return 0
    if args.command == "emit":
        if args.items_csv is None and args.manifest is None:
            raise BatchImportError("emit requires --items-csv and/or --manifest")
        report = build_plan(
            capture_dir=args.capture_dir,
            binding_path=args.binding,
            existing_items_path=args.existing_items,
            as_of=_as_of(args.as_of),
        )
        if args.items_csv is not None:
            rows = write_item_catalog_csv(report, args.items_csv)
            print(f"items_csv={args.items_csv} rows={rows}", file=sys.stderr)
        if args.manifest is not None:
            count = write_cohort_manifest(
                report, args.manifest, capture_dir=args.capture_dir
            )
            print(f"manifest={args.manifest} items={count}", file=sys.stderr)
        return 0
    ledger = run_batch_import(
        intake_history_dir=args.intake_history_dir,
        script=args.script,
        write=args.write,
    )
    _write(ledger, args.output)
    return 0 if ledger["failed_count"] == 0 else 1


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--binding", type=Path)
    parser.add_argument(
        "--existing-items",
        type=Path,
        help=(
            "JSON list of the items already in the database, each with external_key "
            "and name; produced by a read-only query."
        ),
    )
    parser.add_argument("--as-of", type=str)


def build_plan(
    *,
    capture_dir: Path,
    binding_path: Path | None,
    existing_items_path: Path | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Judge every capture in a directory without writing anything anywhere."""

    if not capture_dir.is_dir():
        raise BatchImportError(f"capture directory not found: {capture_dir}")
    bindings = _load_bindings(binding_path)
    existing = _load_existing_items(existing_items_path)
    reviews = [
        _review_capture(
            path=path,
            bindings=bindings,
            existing=existing,
            as_of=as_of,
        )
        for path in sorted(capture_dir.glob("*.json"))
    ]
    by_verdict: dict[str, int] = {}
    for review in reviews:
        by_verdict[review.verdict] = by_verdict.get(review.verdict, 0) + 1
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "catalog_version": DEFAULT_SUPPLY_POOL_CATALOG.version,
        "as_of": as_of,
        "capture_dir": str(capture_dir),
        "capture_count": len(reviews),
        "verdict_counts": dict(sorted(by_verdict.items())),
        "ready_count": by_verdict.get("READY", 0),
        "fully_usable_event_total": sum(
            review.fully_usable_event_count or 0 for review in reviews
        ),
        "captures": [asdict(review) for review in reviews],
        "verdicts": {
            "READY": (
                "valid and usable: bound to the catalog with at least one covered "
                "event, or a negative control that is not expected to bind"
            ),
            "BINDING_REQUIRED": "valid, but no operator binding to a catalogued vehicle",
            "NO_REISSUE_CATALOG_BINDING": (
                "valid and reviewed, but the vehicle has no historical re-release "
                "pool membership; a first-issue limited-event vehicle legitimately "
                "has none, so check the channel coverage before assuming a typo"
            ),
            "EVENT_NOT_COMPLETED": (
                "bound, but every catalogued supply event is still open; re-run "
                "after it closes rather than recapturing"
            ),
            "NO_USABLE_EVENT": (
                "bound, but no completed event has its baseline covered; "
                "recapture reaching further back"
            ),
            "FIRST_ISSUE_NO_PRE_EVENT_MARKET": (
                "bound, but the market history begins inside the earliest "
                "completed event's own window and no later event opened "
                "before it, which is the shape of a vehicle first issued by "
                "that event rather than re-released into a market that "
                "already priced it. An inference, not a certainty: if the "
                "capture was simply truncated, recapture further back and it "
                "will report NO_USABLE_EVENT instead"
            ),
            "INVALID_DOCUMENT": "rejected by the import contract before any upload",
        },
        "network_access": False,
    }


def _review_capture(
    *,
    path: Path,
    bindings: dict[str, dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    as_of: datetime,
) -> CaptureReview:
    codes: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        item_key, source_url, captured_at, daily, hourly = _validate_capture(payload)
    except BatchImportError as exc:
        return CaptureReview(
            file=path.name,
            verdict="INVALID_DOCUMENT",
            item_key=None,
            source_url=None,
            captured_at=None,
            point_count_1d=0,
            point_count_1h=0,
            bound_vehicle_name=None,
            binding_role=None,
            suggested_vehicle_names=(),
            existing_item_id=None,
            existing_item_name=None,
            existing_item_name_binds=None,
            catalog_match_status=None,
            history_start_utc=None,
            history_end_utc=None,
            required_history_start_utc=None,
            covers_required_start=None,
            tradeable_event_count=None,
            fully_usable_event_count=None,
            supply_channel_coverage=None,
            supply_channels_reviewed=None,
            unmodelled_supply_channels=(),
            explanation_codes=(str(exc),),
        )
    except (OSError, json.JSONDecodeError) as exc:
        return CaptureReview(
            file=path.name,
            verdict="INVALID_DOCUMENT",
            item_key=None,
            source_url=None,
            captured_at=None,
            point_count_1d=0,
            point_count_1h=0,
            bound_vehicle_name=None,
            binding_role=None,
            suggested_vehicle_names=(),
            existing_item_id=None,
            existing_item_name=None,
            existing_item_name_binds=None,
            catalog_match_status=None,
            history_start_utc=None,
            history_end_utc=None,
            required_history_start_utc=None,
            covers_required_start=None,
            tradeable_event_count=None,
            fully_usable_event_count=None,
            supply_channel_coverage=None,
            supply_channels_reviewed=None,
            unmodelled_supply_channels=(),
            explanation_codes=(f"unreadable_capture: {type(exc).__name__}",),
        )

    record = existing.get(item_key)
    existing_name = record["name"] if record else None
    existing_binds = (
        DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=1, item_name=existing_name).match_status
        == "MATCHED"
        if existing_name
        else None
    )
    if record is not None and existing_binds is False:
        # The analytics layer binds supply events by item name, so a database row
        # whose name is an operator label silently produces no events.
        codes.append("existing_item_name_does_not_bind_to_catalog")

    binding = bindings.get(item_key)
    if binding is None:
        return CaptureReview(
            file=path.name,
            verdict="BINDING_REQUIRED",
            item_key=item_key,
            source_url=source_url,
            captured_at=captured_at,
            point_count_1d=len(daily),
            point_count_1h=len(hourly),
            bound_vehicle_name=None,
            binding_role=None,
            suggested_vehicle_names=tuple(
                name for name, _ in suggest_catalog_vehicle_names(item_key=item_key)
            ),
            existing_item_id=record["item_id"] if record else None,
            existing_item_name=existing_name,
            existing_item_name_binds=existing_binds,
            catalog_match_status=None,
            history_start_utc=None,
            history_end_utc=None,
            required_history_start_utc=None,
            covers_required_start=None,
            tradeable_event_count=None,
            fully_usable_event_count=None,
            supply_channel_coverage=None,
            supply_channels_reviewed=None,
            unmodelled_supply_channels=(),
            explanation_codes=tuple(
                [*codes, "operator_binding_to_catalogued_vehicle_required"]
            ),
        )

    vehicle_name = binding["vehicle_name"]
    role = binding.get("role", "calibration")
    starts = [
        datetime.fromtimestamp(point[0], UTC) for point in (*daily, *hourly)
    ]
    coverage = assess_history_coverage(
        vehicle_name=vehicle_name,
        bucket_start_times=starts,
        as_of=as_of,
        item_id=record["item_id"] if record else 1,
    )
    codes.extend(coverage.explanation_codes)
    channels = assess_supply_channel_coverage(vehicle_name=vehicle_name)
    codes.extend(channels.explanation_codes)
    if role == "negative_control":
        # A negative control exists to show that an unlisted vehicle binds to no
        # supply event, so failing to match the catalog is the expected result
        # and must not keep it out of the cohort.
        verdict = "READY"
        codes.append("negative_control_not_expected_to_bind")
        if coverage.match_status == "MATCHED":
            codes.append("negative_control_unexpectedly_binds_to_catalog")
    elif coverage.match_status != "MATCHED":
        verdict = "NO_REISSUE_CATALOG_BINDING"
    elif coverage.fully_usable_event_count == 0:
        # A vehicle whose only supply events are still open has nothing to
        # measure yet, which is a reason to come back rather than to recapture.
        if (
            coverage.completed_tradeable_event_count == 0
            and coverage.tradeable_event_count > 0
        ):
            verdict = "EVENT_NOT_COMPLETED"
        elif _first_issue_without_pre_event_market(coverage):
            # Telling an operator to recapture further back is wrong advice
            # when there is nothing further back: the vehicle was created by
            # the event rather than re-released into a market that already
            # priced it.
            verdict = "FIRST_ISSUE_NO_PRE_EVENT_MARKET"
            codes.append("history_begins_after_every_completed_event_opened")
        else:
            verdict = "NO_USABLE_EVENT"
    else:
        verdict = "READY"
    if record is None:
        codes.append("database_item_must_be_created_before_history_import")
    return CaptureReview(
        file=path.name,
        verdict=verdict,
        item_key=item_key,
        source_url=source_url,
        captured_at=captured_at,
        point_count_1d=len(daily),
        point_count_1h=len(hourly),
        bound_vehicle_name=vehicle_name,
        binding_role=role,
        suggested_vehicle_names=(),
        existing_item_id=record["item_id"] if record else None,
        existing_item_name=existing_name,
        existing_item_name_binds=existing_binds,
        catalog_match_status=coverage.match_status,
        history_start_utc=coverage.history_start_utc,
        history_end_utc=coverage.history_end_utc,
        required_history_start_utc=coverage.required_history_start_utc,
        covers_required_start=coverage.covers_required_start,
        tradeable_event_count=coverage.tradeable_event_count,
        fully_usable_event_count=coverage.fully_usable_event_count,
        supply_channel_coverage=channels.coverage,
        supply_channels_reviewed=channels.reviewed,
        unmodelled_supply_channels=channels.unmodelled_supply_channels,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _first_issue_without_pre_event_market(coverage: Any) -> bool:
    """True when the market itself appears to have begun with the event.

    Distinguishes a capture that starts too late from a vehicle that never
    traded before the event that issued it. "Starts after every completed
    event" is not enough on its own, because a badly truncated capture of an
    old vehicle satisfies it too. The discriminating shape is that trading
    began *inside* the earliest completed event's own window, which is what
    happens when that event created the coupons being sold.

    It stays an inference. A truncated capture that happens to begin inside an
    event window will be mislabelled, and recapturing further back moves it
    back to ``NO_USABLE_EVENT``.
    """

    start = coverage.history_start_utc
    if start is None:
        return False
    completed = sorted(
        (value for value in coverage.events if value.event_completed),
        key=lambda value: value.opens_at,
    )
    if not completed:
        return False
    if any(value.opens_at > start for value in completed):
        return False
    earliest = completed[0]
    return earliest.opens_at <= start <= earliest.closes_at


def _validate_capture(
    payload: Any,
) -> tuple[str, str, datetime, list[Any], list[Any]]:
    if not isinstance(payload, dict):
        raise BatchImportError("capture_must_be_a_json_object")
    if set(payload) != CAPTURE_FIELDS:
        missing = sorted(CAPTURE_FIELDS - set(payload))
        extra = sorted(set(payload) - CAPTURE_FIELDS)
        raise BatchImportError(
            f"capture_field_mismatch missing={missing} unexpected={extra}"
        )
    if payload["schema_version"] != HISTORY_SCHEMA_VERSION:
        raise BatchImportError("unsupported_schema_version")
    item_key = payload["item_key"]
    if not isinstance(item_key, str) or not item_key.strip() or len(item_key) > 512:
        raise BatchImportError("item_key_invalid")
    source_url = payload["source_url"]
    if not isinstance(source_url, str):
        raise BatchImportError("source_url_invalid")
    parsed = urlsplit(source_url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "trade.gaijin.net"
        or len(parts) != 3
        or parts[0] != "market"
        or parts[1] != SUPPORTED_MARKET_ID
    ):
        raise BatchImportError("source_url_not_a_supported_market_item_page")
    captured_at = _parse_datetime(payload["captured_at"], "captured_at")
    if captured_at > datetime.now(UTC) + timedelta(minutes=5):
        raise BatchImportError("captured_at_in_future")
    series = payload["series"]
    if not isinstance(series, dict) or set(series) != set(GRANULARITY_SECONDS):
        raise BatchImportError("series_must_contain_exactly_1h_and_1d")
    daily = _validate_series(series["1d"], "1d")
    hourly = _validate_series(series["1h"], "1h")
    if not daily and not hourly:
        raise BatchImportError("series_empty")
    return item_key.strip(), source_url.strip(), captured_at, daily, hourly


def _validate_series(points: Any, label: str) -> list[Any]:
    if not isinstance(points, list):
        raise BatchImportError(f"series_{label}_must_be_a_list")
    if len(points) > MAX_POINTS_PER_SERIES:
        raise BatchImportError(f"series_{label}_too_many_points")
    duration = GRANULARITY_SECONDS[label]
    timestamps: list[int] = []
    for point in points:
        if not isinstance(point, list) or len(point) != 3:
            raise BatchImportError(f"series_{label}_point_shape_invalid")
        timestamp, price_raw, volume = point
        for value in (timestamp, price_raw, volume):
            if isinstance(value, bool) or not isinstance(value, int):
                raise BatchImportError(f"series_{label}_point_must_be_integers")
        if not MIN_UNIX_SECONDS <= timestamp <= MAX_UNIX_SECONDS:
            raise BatchImportError(f"series_{label}_timestamp_out_of_range")
        if timestamp % duration != 0:
            raise BatchImportError(f"series_{label}_timestamp_not_aligned")
        if not 0 < price_raw <= MAX_PRICE_RAW:
            raise BatchImportError(f"series_{label}_price_out_of_range")
        if not 0 < volume <= MAX_REPORTED_VOLUME:
            raise BatchImportError(f"series_{label}_volume_out_of_range")
        timestamps.append(timestamp)
    if len(timestamps) != len(set(timestamps)):
        raise BatchImportError(f"series_{label}_duplicate_timestamp")
    if timestamps != sorted(timestamps):
        raise BatchImportError(f"series_{label}_not_sorted")
    return list(points)


def write_item_catalog_csv(report: dict[str, Any], output: Path) -> int:
    """Write catalog rows for bound captures whose item does not exist yet.

    Rows for items already in the database are omitted on purpose: the catalog
    importer fails the whole file on any metadata mismatch, and an existing row
    whose name is an operator label would mismatch the catalogued vehicle name.
    """

    rows = [
        {
            "external_key": capture["item_key"],
            "name": capture["bound_vehicle_name"],
            "category": "vehicle",
            "rarity": "",
            "is_active": "true",
        }
        for capture in report["captures"]
        if capture["verdict"] == "READY"
        and capture["existing_item_id"] is None
        and capture["binding_role"] != "negative_control"
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATALOG_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def write_cohort_manifest(
    report: dict[str, Any],
    output: Path,
    *,
    capture_dir: Path,
) -> int:
    """Write a replay manifest for ready captures whose item already exists.

    A capture whose database item has not been created yet is left out rather
    than given an invented id, so the manifest never claims an identity the
    database does not have.
    """

    items = []
    for capture in report["captures"]:
        if capture["verdict"] != "READY" or capture["existing_item_id"] is None:
            continue
        items.append(
            {
                "item_id": capture["existing_item_id"],
                "item_name": capture["bound_vehicle_name"],
                "history_path": capture["file"],
                "as_of": _isoformat(report["as_of"]),
                "role": capture["binding_role"] or "calibration",
            }
        )
    if not items:
        raise BatchImportError(
            "no ready capture has a database item yet; import the catalog first"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"schema_version": MANIFEST_SCHEMA_VERSION, "items": items},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(items)


def run_batch_import(
    *,
    intake_history_dir: Path,
    script: Path,
    write: bool,
) -> dict[str, Any]:
    """Drive the audited per-file import script once for each inbox capture.

    A failing file is recorded and the batch continues, matching the intake
    rule that failed files stay in the inbox for operator review and are never
    silently retried.
    """

    if not intake_history_dir.is_dir():
        raise BatchImportError(f"intake history directory not found: {intake_history_dir}")
    results: list[dict[str, Any]] = []
    for path in sorted(intake_history_dir.glob("*.json")):
        command = [str(script), str(path)]
        if write:
            command.append("--write")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        summary: Any = None
        for line in reversed(completed.stdout.splitlines()):
            try:
                summary = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        results.append(
            {
                "file": path.name,
                "exit_code": completed.returncode,
                "succeeded": completed.returncode == 0,
                "summary": summary,
                "stderr_tail": completed.stderr.strip().splitlines()[-3:],
            }
        )
    failed = sum(1 for value in results if not value["succeeded"])
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "mode": "write" if write else "dry_run",
        "intake_history_dir": str(intake_history_dir),
        "script": str(script),
        "file_count": len(results),
        "succeeded_count": len(results) - failed,
        "failed_count": failed,
        "results": results,
    }


def _load_bindings(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise BatchImportError("unsupported binding schema_version")
    entries = payload.get("bindings")
    if not isinstance(entries, list):
        raise BatchImportError("binding file must contain a bindings list")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        item_key = entry.get("item_key")
        vehicle_name = entry.get("vehicle_name")
        role = entry.get("role", "calibration")
        if not isinstance(item_key, str) or not item_key.strip():
            raise BatchImportError("binding item_key must be a non-empty string")
        if not isinstance(vehicle_name, str) or not vehicle_name.strip():
            raise BatchImportError("binding vehicle_name must be a non-empty string")
        if role not in ROLES:
            raise BatchImportError(f"binding role must be one of {ROLES}")
        if item_key.strip() in result:
            raise BatchImportError("binding item_key values must be unique")
        result[item_key.strip()] = {
            "vehicle_name": vehicle_name.strip(),
            "role": role,
        }
    return result


def _load_existing_items(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise BatchImportError("existing items file must contain a JSON list")
    result: dict[str, dict[str, Any]] = {}
    for entry in payload:
        external_key = entry.get("external_key")
        if not isinstance(external_key, str) or not external_key.strip():
            raise BatchImportError("existing item external_key must be a string")
        result[external_key.strip()] = {
            "item_id": entry.get("id") if isinstance(entry.get("id"), int) else None,
            "name": entry.get("name"),
        }
    return result


def _as_of(value: str | None) -> datetime:
    return _parse_datetime(value, "--as-of") if value else datetime.now(UTC)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise BatchImportError(f"{field_name}_must_be_an_iso8601_string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchImportError(f"{field_name}_is_not_iso8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatchImportError(f"{field_name}_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _write(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(_normalize(payload), ensure_ascii=False, indent=2) + "\n"
    if output is None:
        print(text, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return _isoformat(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
