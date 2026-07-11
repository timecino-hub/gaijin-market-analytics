"""Offline CLI for strict Gaijin historical JSON inventory generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from gaijin_market_analytics.datasets.gaijin_history_json import (
    OfflineHistoryValidationError,
    load_offline_history_dataset,
)
from gaijin_market_analytics.datasets.history_inventory import (
    build_history_inventory,
    render_history_inventory_markdown,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an offline inventory from strict Gaijin historical JSON files."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        dataset = load_offline_history_dataset(
            args.manifest,
            input_root=args.input_root,
        )
        inventory = build_history_inventory(dataset)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        indent = 2 if args.pretty else None
        separators = None if args.pretty else (",", ":")
        encoded = json.dumps(
            inventory,
            ensure_ascii=False,
            indent=indent,
            separators=separators,
            sort_keys=True,
        )
        (args.output_dir / "round6-json-inventory.json").write_text(
            encoded + "\n",
            encoding="utf-8",
        )
        (args.output_dir / "ROUND6_JSON_INVENTORY.md").write_text(
            render_history_inventory_markdown(inventory),
            encoding="utf-8",
        )
    except OfflineHistoryValidationError as exc:
        print(f"offline_history_error:{exc.code}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"offline_history_io_error:{type(exc).__name__}", file=sys.stderr)
        return 3
    print(
        "offline_history_inventory_ok "
        f"samples={len(dataset.samples)} "
        f"source_manifest_sha256={dataset.source_manifest_sha256} "
        f"typed_trade_dataset_sha256={dataset.typed_trade_dataset_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
