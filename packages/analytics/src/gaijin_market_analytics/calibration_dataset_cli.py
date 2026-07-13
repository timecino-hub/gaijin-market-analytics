"""Local-only CLI for Round 6B combined calibration dataset generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from gaijin_market_analytics.datasets.combined_calibration import (
    CombinedCalibrationValidationError,
    build_combined_calibration_dataset,
    write_combined_calibration_dataset,
)
from gaijin_market_analytics.datasets.gaijin_history_json import (
    OfflineHistoryValidationError,
    load_offline_history_dataset,
)
from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryExportValidationError,
    load_item_mapping_manifest,
    load_market_history_export,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combine validated offline trade history and reviewed market-history "
            "exports using an explicit user-confirmed item mapping."
        )
    )
    parser.add_argument("--history-manifest", required=True, type=Path)
    parser.add_argument("--history-input-root", type=Path)
    parser.add_argument("--market-history-export", required=True, type=Path)
    parser.add_argument("--item-mapping", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        historical = load_offline_history_dataset(
            args.history_manifest,
            input_root=args.history_input_root,
        )
        market = load_market_history_export(args.market_history_export)
        mapping = load_item_mapping_manifest(args.item_mapping)
        payload = build_combined_calibration_dataset(
            historical_dataset=historical,
            market_dataset=market,
            item_mapping=mapping,
        )
        write_combined_calibration_dataset(
            args.output,
            payload,
            pretty=args.pretty,
        )
    except OfflineHistoryValidationError as exc:
        print(f"combined_history_error:{exc.code}", file=sys.stderr)
        return 2
    except MarketHistoryExportValidationError as exc:
        print(f"combined_market_error:{exc.code}", file=sys.stderr)
        return 3
    except CombinedCalibrationValidationError as exc:
        print(f"combined_dataset_error:{exc.code}", file=sys.stderr)
        return 4
    except OSError as exc:
        print(f"combined_dataset_io_error:{type(exc).__name__}", file=sys.stderr)
        return 5
    print(
        "combined_calibration_ok "
        f"items={payload['diagnostics']['combined_item_count']} "
        f"observations={payload['diagnostics']['observation_count']} "
        f"trade_buckets={payload['diagnostics']['trade_bucket_count']} "
        f"combined_source_fingerprint={payload['combined_source_fingerprint']} "
        f"combined_typed_fingerprint={payload['combined_typed_fingerprint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
