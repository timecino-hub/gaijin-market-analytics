from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api.screen_recognition.ground_truth import (
    GroundTruthInvalidError,
    make_ground_truth_template,
)
from api.screen_recognition.image_io import list_image_files
from api.screen_recognition.json_util import dumps_json
from api.screen_recognition.config import default_paired_cut_config
from api.screen_recognition.paired_runner import PairedCutRunConfig, run_paired_cut
from api.screen_recognition.pairs import make_paired_ground_truth_template
from api.screen_recognition.runner import CutRunConfig, run_cut


EXIT_PARAMETER_ERROR = 2
EXIT_ACCEPTANCE_FAILED = 3
EXIT_GROUND_TRUTH_INVALID = 4


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "init":
        _run_init(args)
        return
    if args.command == "run":
        _run_cut(args)
        return
    if args.command == "init-paired":
        _run_init_paired(args)
        return
    if args.command == "run-paired":
        _run_paired_cut(args)
        return
    parser.print_help()


def _run_init(args: argparse.Namespace) -> None:
    images_dir = Path(args.images_dir)
    output = Path(args.output)
    rows = make_ground_truth_template(list_image_files(images_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(dumps_json(row, pretty=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"Generated {len(rows)} ground truth template rows.", file=sys.stderr)


def _run_cut(args: argparse.Namespace) -> None:
    try:
        result = run_cut(
            CutRunConfig(
                images_dir=Path(args.images_dir),
                ground_truth_path=Path(args.ground_truth),
                output_dir=Path(args.output_dir),
                layout_profile_name=args.layout_profile,
                ocr_backend_name=args.ocr_backend,
                strict=args.strict,
                pretty=args.pretty,
                fail_fast=args.fail_fast,
                run_id=args.run_id,
                debug_artifacts=args.debug_artifacts,
            )
        )
    except GroundTruthInvalidError as exc:
        print(f"Ground truth invalid: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_GROUND_TRUTH_INVALID) from exc
    except ValueError as exc:
        print(f"Invalid CUT configuration: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_PARAMETER_ERROR) from exc
    print(
        "CUT run complete: "
        f"overall_status={result.summary['overall_status']} "
        f"processed_images={result.summary['processed_images']} "
        f"passed={result.summary['passed']} "
        f"failed={result.summary['failed']}",
        file=sys.stderr,
    )
    if args.strict and result.summary["overall_status"] != "passed":
        raise SystemExit(EXIT_ACCEPTANCE_FAILED)


def _run_init_paired(args: argparse.Namespace) -> None:
    images_dir = Path(args.images_dir)
    output = Path(args.output)
    rows, errors = make_paired_ground_truth_template(
        images_dir, config=default_paired_cut_config()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(dumps_json(row, pretty=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(
        f"Generated {len(rows)} paired ground truth template rows; "
        f"global_pair_errors={len(errors)}.",
        file=sys.stderr,
    )
    for error in sorted(set(errors)):
        print(f"Pair scan warning: {error}", file=sys.stderr)


def _run_paired_cut(args: argparse.Namespace) -> None:
    try:
        result = run_paired_cut(
            PairedCutRunConfig(
                images_dir=Path(args.images_dir),
                ground_truth_path=Path(args.ground_truth),
                output_dir=Path(args.output_dir),
                current_layout_name=args.current_layout,
                history_layout_name=args.history_layout,
                ocr_backend_name=args.ocr_backend,
                strict=args.strict,
                pretty=args.pretty,
                fail_fast=args.fail_fast,
                run_id=args.run_id,
                debug_artifacts=args.debug_artifacts,
            )
        )
    except GroundTruthInvalidError as exc:
        print(f"Ground truth invalid: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_GROUND_TRUTH_INVALID) from exc
    except ValueError as exc:
        print(f"Invalid paired CUT configuration: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_PARAMETER_ERROR) from exc
    print(
        "Paired CUT run complete: "
        f"total_pairs={result.pair_summary['total_pairs']} "
        f"combined_passed={result.pair_summary['combined_passed']} "
        f"combined_failed={result.pair_summary['combined_failed']}",
        file=sys.stderr,
    )
    if args.strict and result.pair_summary["combined_failed"] > 0:
        raise SystemExit(EXIT_ACCEPTANCE_FAILED)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Screen Recognition CUT-20 developer tooling.")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="Generate a ground truth JSONL template.")
    init.add_argument("--images-dir", required=True)
    init.add_argument("--output", required=True)

    run = subparsers.add_parser("run", help="Run Screen Recognition CUT-20.")
    run.add_argument("--images-dir", required=True)
    run.add_argument("--ground-truth", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--layout-profile", default="gaijin-market-desktop-v1")
    run.add_argument("--ocr-backend", default="windows-ocr")
    run.add_argument("--strict", action="store_true")
    run.add_argument("--pretty", action="store_true")
    run.add_argument("--fail-fast", action="store_true")
    run.add_argument("--run-id")
    run.add_argument("--debug-artifacts", action="store_true")

    init_paired = subparsers.add_parser(
        "init-paired", help="Generate a paired CUT-20 ground truth JSONL template."
    )
    init_paired.add_argument("--images-dir", required=True)
    init_paired.add_argument("--output", required=True)

    run_paired = subparsers.add_parser("run-paired", help="Run Screen Recognition Paired CUT-20.")
    run_paired.add_argument("--images-dir", required=True)
    run_paired.add_argument("--ground-truth", required=True)
    run_paired.add_argument("--output-dir", required=True)
    run_paired.add_argument("--current-layout", default="gaijin-market-desktop-v1")
    run_paired.add_argument("--history-layout", default="gaijin-market-history-v1")
    run_paired.add_argument("--ocr-backend", default="windows-ocr")
    run_paired.add_argument("--strict", action="store_true")
    run_paired.add_argument("--pretty", action="store_true")
    run_paired.add_argument("--fail-fast", action="store_true")
    run_paired.add_argument("--run-id")
    run_paired.add_argument("--debug-artifacts", action="store_true")
    return parser


if __name__ == "__main__":
    main()
