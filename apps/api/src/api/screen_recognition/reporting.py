from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from api.screen_recognition.contracts import SampleResult
from api.screen_recognition.json_util import dump_json_file, dumps_json


def write_outputs(
    *,
    output_dir: Path,
    run_metadata: dict[str, Any],
    results: list[SampleResult],
    summary: dict[str, Any],
    pretty: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    failures_dir = output_dir / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    dump_json_file(output_dir / "run_metadata.json", run_metadata, pretty=pretty)
    if "current_config" in run_metadata:
        dump_json_file(
            output_dir / "effective_current_config.json",
            run_metadata["current_config"],
            pretty=pretty,
        )
    dump_json_file(output_dir / "summary.json", summary, pretty=pretty)
    (output_dir / "results.jsonl").write_text(
        "".join(dumps_json(result.to_json(), pretty=False) + "\n" for result in results),
        encoding="utf-8",
    )
    _write_results_csv(output_dir / "results.csv", results)
    _write_report(output_dir / "report.md", run_metadata, summary, results)
    for result in results:
        if result.errors:
            dump_json_file(
                failures_dir / f"{_safe_sample_id(result.sample_id)}.json",
                {
                    "sample_id": result.sample_id,
                    "filename": result.filename,
                    "status": result.status.value,
                    "errors": list(result.errors),
                    "warnings": list(result.warnings),
                    "field_comparisons": [
                        comparison.to_json() for comparison in result.field_comparisons
                    ],
                    "raw_ocr": {
                        name: evidence.to_json()
                        for name, evidence in sorted(result.raw_ocr.items())
                    },
                },
                pretty=pretty,
            )


def _write_results_csv(path: Path, results: list[SampleResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "sample_id",
                "filename",
                "status",
                "layout_match",
                "errors",
                "warnings",
                "processing_duration_ms",
                "used_sidecar_ocr_text",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "sample_id": result.sample_id,
                    "filename": result.filename,
                    "status": result.status.value,
                    "layout_match": result.layout_match,
                    "errors": ";".join(result.errors),
                    "warnings": ";".join(result.warnings),
                    "processing_duration_ms": result.processing_duration_ms,
                    "used_sidecar_ocr_text": result.used_sidecar_ocr_text,
                }
            )


def _write_report(
    path: Path,
    run_metadata: dict[str, Any],
    summary: dict[str, Any],
    results: list[SampleResult],
) -> None:
    counter = Counter(code for result in results for code in result.errors)
    lines = [
        "# Screen Recognition CUT-20 Report",
        "",
        f"- Run ID: `{run_metadata['run_id']}`",
        f"- Test scope: `{run_metadata['test_scope']}`",
        f"- OCR backend: `{run_metadata['ocr_backend']['name']}` `{run_metadata['ocr_backend']['version']}`",
        f"- Layout profile: `{run_metadata['layout_profile']['name']}` `{run_metadata['layout_profile']['version']}`",
        f"- Overall status: `{summary['overall_status']}`",
        "",
        "## Summary",
        "",
    ]
    for key in sorted(summary):
        lines.append(f"- `{key}`: `{summary[key]}`")
    lines.extend(["", "## Most Common Errors", ""])
    if counter:
        for code, count in counter.most_common(10):
            lines.append(f"- `{code}`: {count}")
    else:
        lines.append("- None")
    lines.extend(["", "## Failed Samples", ""])
    failed = [result for result in results if result.errors]
    if not failed:
        lines.append("- None")
    for result in failed:
        lines.append(
            f"- `{result.sample_id}` `{result.filename}`: {result.status.value}; "
            f"errors={', '.join(result.errors)}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_sample_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
