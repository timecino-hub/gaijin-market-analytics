from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.screen_recognition.comparison import (
    DEFAULT_THRESHOLDS,
    compare_contracts,
    determine_sample_status,
    summarize_results,
)
from api.screen_recognition.config import default_current_cut_config, git_metadata
from api.screen_recognition.contracts import (
    CUT_RUNNER_VERSION,
    CutStatus,
    GroundTruthEntry,
    ImageInfo,
    PARSER_VERSION,
    SampleResult,
    ScreenContract,
    TestScope,
    stable_issue_codes,
)
from api.screen_recognition.ground_truth import GroundTruthInvalidError, load_ground_truth
from api.screen_recognition.image_io import ImageReadError, list_image_files, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrBackendNotConfiguredError,
    OcrInvocation,
    WindowsOcrRecognizer,
    get_recognizer,
    windows_ocr_preprocessing_metadata,
)
from api.screen_recognition.ocr_candidates import (
    PRICE_SELECTION_POLICY_PRICE_CELLS_V3,
)
from api.screen_recognition.parser import parse_ocr_contract
from api.screen_recognition.reporting import write_outputs


@dataclass(frozen=True)
class CutRunConfig:
    images_dir: Path
    ground_truth_path: Path
    output_dir: Path
    layout_profile_name: str
    ocr_backend_name: str
    strict: bool = False
    pretty: bool = False
    fail_fast: bool = False
    run_id: str | None = None
    debug_artifacts: bool = False


@dataclass(frozen=True)
class CutRunResult:
    run_metadata: dict[str, Any]
    summary: dict[str, Any]
    results: tuple[SampleResult, ...]


def run_cut(config: CutRunConfig) -> CutRunResult:
    started_at = datetime.now(UTC)
    run_id = config.run_id or f"cut20-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = load_ground_truth(config.ground_truth_path)
    files_found = len(list_image_files(config.images_dir))
    profile = get_layout_profile(config.layout_profile_name)
    recognizer = get_recognizer(config.ocr_backend_name)
    current_config = default_current_cut_config(
        layout_profile_name=profile.name,
        ocr_backend_name=recognizer.backend_name,
    )
    test_scope = (
        TestScope.PARSER_ONLY.value
        if recognizer.test_scope == TestScope.PARSER_ONLY.value
        else TestScope.END_TO_END.value
    )
    results: list[SampleResult] = []
    metadata_base = {
        "run_id": run_id,
        "runner_version": CUT_RUNNER_VERSION,
        "started_at": started_at,
        "test_scope": test_scope,
        "ocr_backend": {
            "name": recognizer.backend_name,
            "version": recognizer.backend_version,
            "runs_locally": True,
            "runtime_network_access": False,
            "runtime_model_download": False,
        },
        "layout_profile": profile.to_json(),
        "parser": {"version": PARSER_VERSION},
        "current_config": current_config.to_json(),
        "config_sha256": current_config.sha256(),
        "git": git_metadata(),
        "preprocessing": windows_ocr_preprocessing_metadata()
        if recognizer.backend_name == "windows-ocr"
        else {"source_image_modified": False},
        "thresholds": DEFAULT_THRESHOLDS,
        "debug_artifacts_enabled": config.debug_artifacts,
        "private_paths_recorded": False,
        "database_access": False,
        "network_access": False,
        "candidate_csv_supported": False,
        "image_count": len(entries),
        "files_found": files_found,
    }
    for entry in entries:
        result = _process_entry(
            config=config,
            entry=entry,
            run_id=run_id,
            profile=profile,
            recognizer_name=recognizer.backend_name,
        )
        results.append(result)
        if config.fail_fast and result.status not in {
            CutStatus.PASSED,
            CutStatus.PASSED_WITH_WARNING,
        }:
            break
    summary = summarize_results(
        results=results, files_found=files_found, ground_truth_entries=len(entries)
    )
    finished_at = datetime.now(UTC)
    run_metadata = {
        **metadata_base,
        "finished_at": finished_at,
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
    }
    write_outputs(
        output_dir=output_dir,
        run_metadata=run_metadata,
        results=results,
        summary=summary,
        pretty=config.pretty,
    )
    return CutRunResult(run_metadata=run_metadata, summary=summary, results=tuple(results))


def load_ground_truth_checked(path: Path) -> list[GroundTruthEntry]:
    try:
        return load_ground_truth(path)
    except GroundTruthInvalidError:
        raise


def _process_entry(
    *,
    config: CutRunConfig,
    entry: GroundTruthEntry,
    run_id: str,
    profile: Any,
    recognizer_name: str,
) -> SampleResult:
    started = time.perf_counter()
    warnings: list[str] = []
    errors: list[str] = []
    comparisons = []
    image_info: ImageInfo | None = None
    layout_match = False
    raw_ocr = {}
    recognized = ScreenContract(
        item_key=entry.item_key,
        item_key_source="ground_truth_manifest" if entry.item_key else None,
    )
    try:
        image_path = config.images_dir / entry.filename
        image_info = read_image_info(image_path)
        validate_layout_match(profile, image_info)
        layout_match = True
        debug_dir = (
            config.output_dir / "debug_artifacts" / run_id / entry.sample_id
            if config.debug_artifacts
            else None
        )
        recognizer = get_recognizer(config.ocr_backend_name)
        ocr_result = recognizer.recognize(
            OcrInvocation(
                image_path=image_path,
                layout_profile=profile,
                debug_artifacts_dir=debug_dir,
            )
        )
        raw_ocr = ocr_result.fields
        warnings.extend(ocr_result.warnings)
        if ocr_result.backend_version in {
            WindowsOcrRecognizer.price_cells_v3_backend_version,
            WindowsOcrRecognizer.price_cells_v4_backend_version,
        }:
            recognized, parse_warnings, parse_errors = parse_ocr_contract(
                ocr_result.fields,
                item_key=entry.item_key,
                price_selection_policy=PRICE_SELECTION_POLICY_PRICE_CELLS_V3,
            )
        else:
            recognized, parse_warnings, parse_errors = parse_ocr_contract(
                ocr_result.fields,
                item_key=entry.item_key,
            )
        warnings.extend(parse_warnings)
        errors.extend(parse_errors)
        comparisons, compare_warnings, compare_errors = compare_contracts(entry, recognized)
        warnings.extend(compare_warnings)
        errors.extend(compare_errors)
    except ImageReadError:
        errors.append("image_unreadable")
    except LayoutUnsupportedError:
        errors.append("unsupported_layout")
    except OcrBackendNotConfiguredError:
        errors.append("image_recognizer_not_configured")
    except OcrBackendError:
        errors.append("ocr_backend_error")
    except Exception:
        errors.append("unexpected_exception")
    duration_ms = int((time.perf_counter() - started) * 1000)
    stable_errors = tuple(stable_issue_codes(errors))
    stable_warnings = tuple(stable_issue_codes(warnings))
    status = determine_sample_status(
        list(stable_errors), list(stable_warnings), expected_status=entry.expected_status
    )
    return SampleResult(
        sample_id=entry.sample_id,
        filename=entry.filename,
        status=status,
        image_info=image_info,
        layout_profile=profile.name,
        layout_match=layout_match,
        recognized=recognized,
        expected=entry.expected,
        field_comparisons=tuple(comparisons),
        raw_ocr=raw_ocr,
        warnings=stable_warnings,
        errors=stable_errors,
        processing_duration_ms=duration_ms,
        recognizer_version=recognizer_name,
        parser_version=PARSER_VERSION,
        used_sidecar_ocr_text=config.ocr_backend_name == "sidecar",
    )
