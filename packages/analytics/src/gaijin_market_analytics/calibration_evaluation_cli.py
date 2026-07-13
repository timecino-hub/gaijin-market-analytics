"""Local-only CLI for deterministic Round 6C calibration evaluation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from gaijin_market_analytics.calibration.evaluation import (
    CalibrationEvaluationValidationError,
    build_calibration_evaluation_dataset,
    load_calibration_evaluation_config,
    write_calibration_evaluation_dataset,
)
from gaijin_market_analytics.datasets.combined_calibration import (
    CombinedCalibrationValidationError,
    load_combined_calibration_dataset,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic no-lookahead quote evaluation and descriptive "
            "trade-support comparisons from a validated Round 6B dataset."
        )
    )
    parser.add_argument("--combined-dataset", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        combined = load_combined_calibration_dataset(args.combined_dataset)
        config = load_calibration_evaluation_config(args.config)
        payload = build_calibration_evaluation_dataset(
            combined_dataset=combined,
            config=config,
        )
        write_calibration_evaluation_dataset(
            args.output,
            payload,
            pretty=args.pretty,
        )
    except CombinedCalibrationValidationError as exc:
        print(f"calibration_combined_error:{exc.code}", file=sys.stderr)
        return 2
    except CalibrationEvaluationValidationError as exc:
        print(f"calibration_evaluation_error:{exc.code}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"calibration_evaluation_io_error:{type(exc).__name__}", file=sys.stderr)
        return 4
    fingerprints = payload["fingerprints"]
    print(
        "calibration_evaluation_ok "
        f"examples={payload['diagnostics']['included_example_count']} "
        f"folds={payload['diagnostics']['fold_count']} "
        f"targets={payload['diagnostics']['target_available_count']} "
        "quote_only_features_fingerprint="
        f"{fingerprints['quote_only_features_fingerprint']} "
        "trade_support_features_fingerprint="
        f"{fingerprints['trade_support_features_fingerprint']} "
        "evaluation_report_fingerprint="
        f"{fingerprints['evaluation_report_fingerprint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
