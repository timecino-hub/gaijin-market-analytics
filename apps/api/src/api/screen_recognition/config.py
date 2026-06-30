from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from api.screen_recognition.contracts import CUT_RUNNER_VERSION


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
        payload = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_paired_cut_config() -> PairedCutConfig:
    return PairedCutConfig()


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
