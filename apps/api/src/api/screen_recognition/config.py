from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from api.screen_recognition.contracts import CUT_RUNNER_VERSION, PARSER_VERSION
from api.screen_recognition.layouts import get_layout_profile
from api.screen_recognition.ocr_backend import windows_ocr_preprocessing_metadata
from api.screen_recognition.ocr_candidates import FIELD_OCR_PIPELINES, PRICE_SELECTION_ORDER


CURRENT_CANDIDATE_SELECTION_RULE_VERSION = "field-specific-ocr-candidates/1.0.0"
CURRENT_PRICE_VALIDATION_RULE_VERSION = "market-price-decimal-evidence/1.0.0"
CURRENT_QUANTITY_VALIDATION_RULE_VERSION = "summary-anchored-quantity/1.0.0"
CURRENT_QUANTITY_SELECTION_ORDER = (
    "independent_quantity_source_agreement",
    "single_label_anchored_quantity",
    "single_compact_quantity",
)


def stable_config_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ColorMaskThreshold:
    min_r: int
    max_r: int
    min_g: int
    max_g: int
    min_b: int
    max_b: int
    min_delta: int

    def to_json(self) -> dict[str, int]:
        return {
            "min_r": self.min_r,
            "max_r": self.max_r,
            "min_g": self.min_g,
            "max_g": self.max_g,
            "min_b": self.min_b,
            "max_b": self.max_b,
            "min_delta": self.min_delta,
        }


@dataclass(frozen=True)
class PairedCutConfig:
    current_layout_name: str = "gaijin-market-desktop-v1"
    history_layout_name: str = "gaijin-market-history-v1"
    red_mask: ColorMaskThreshold = ColorMaskThreshold(120, 255, 0, 150, 0, 150, 40)
    blue_mask: ColorMaskThreshold = ColorMaskThreshold(0, 150, 0, 180, 100, 255, 35)
    axis_mapping_max_residual_px: Decimal = Decimal("8")
    min_axis_ticks: int = 2
    calibration_sample_ids: tuple[str, ...] = ("001", "002", "003", "004")
    evaluation_sample_ids: tuple[str, ...] = tuple(f"{index:03d}" for index in range(5, 21))
    red_extraction_method: str = "red_area_upper_envelope"
    blue_extraction_method: str = "blue_line_median_y"
    sample_normalized_x: tuple[Decimal, ...] = (
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("0.75"),
    )

    def split_for(self, sample_id: str) -> str | None:
        if sample_id in self.calibration_sample_ids:
            return "calibration"
        if sample_id in self.evaluation_sample_ids:
            return "evaluation"
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "cut_runner_version": CUT_RUNNER_VERSION,
            "current_layout_name": self.current_layout_name,
            "history_layout_name": self.history_layout_name,
            "red_mask": self.red_mask.to_json(),
            "blue_mask": self.blue_mask.to_json(),
            "axis_mapping_max_residual_px": str(self.axis_mapping_max_residual_px),
            "min_axis_ticks": self.min_axis_ticks,
            "calibration_sample_ids": list(self.calibration_sample_ids),
            "evaluation_sample_ids": list(self.evaluation_sample_ids),
            "red_extraction_method": self.red_extraction_method,
            "blue_extraction_method": self.blue_extraction_method,
            "sample_normalized_x": [str(value) for value in self.sample_normalized_x],
        }

    def sha256(self) -> str:
        return stable_config_sha256(self.to_json())


def default_paired_cut_config() -> PairedCutConfig:
    return PairedCutConfig()


@dataclass(frozen=True)
class CurrentCutConfig:
    layout_profile_name: str = "gaijin-market-desktop-v1"
    ocr_backend_name: str = "windows-ocr"
    ocr_languages: tuple[str, ...] = ("zh-Hans", "en")
    candidate_selection_rule_version: str = CURRENT_CANDIDATE_SELECTION_RULE_VERSION
    price_validation_rule_version: str = CURRENT_PRICE_VALIDATION_RULE_VERSION
    quantity_validation_rule_version: str = CURRENT_QUANTITY_VALIDATION_RULE_VERSION

    def to_json(self) -> dict[str, Any]:
        layout_profile = get_layout_profile(self.layout_profile_name)
        return {
            "layout_profile": layout_profile.to_json(),
            "item_name_pipeline": list(FIELD_OCR_PIPELINES["item_name"]),
            "price_pipeline": list(FIELD_OCR_PIPELINES["price"]),
            "quantity_pipeline": list(FIELD_OCR_PIPELINES["quantity"]),
            "preprocessing_variants": windows_ocr_preprocessing_metadata(),
            "ocr_backend": {
                "name": self.ocr_backend_name,
                "languages": list(self.ocr_languages),
            },
            "candidate_selection": {
                "version": self.candidate_selection_rule_version,
                "price_selection_order": list(PRICE_SELECTION_ORDER),
                "quantity_selection_order": list(CURRENT_QUANTITY_SELECTION_ORDER),
            },
            "parser": {"version": PARSER_VERSION},
            "runner": {"version": CUT_RUNNER_VERSION},
            "validation_rules": {
                "price": self.price_validation_rule_version,
                "quantity": self.quantity_validation_rule_version,
            },
        }

    def sha256(self) -> str:
        return stable_config_sha256(self.to_json())


def default_current_cut_config(
    *,
    layout_profile_name: str = "gaijin-market-desktop-v1",
    ocr_backend_name: str = "windows-ocr",
) -> CurrentCutConfig:
    return CurrentCutConfig(
        layout_profile_name=layout_profile_name,
        ocr_backend_name=ocr_backend_name,
    )


def git_metadata() -> dict[str, Any]:
    commit = _git_output(["git", "rev-parse", "HEAD"])
    status = _git_output(["git", "status", "--short"])
    return {
        "commit": commit,
        "worktree_dirty": bool(status),
    }


def _git_output(command: list[str]) -> str | None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
