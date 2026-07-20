"""Offline CLI for the database-free manual order-book price read model."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NoReturn, Sequence

from api.importers.manual_order_book_json import (
    ManualOrderBookValidationError,
    read_manual_order_book_file,
)
from api.pricing.manual_order_book_price import PriceInterpretationError
from api.pricing.manual_order_book_read_model import (
    ManualOrderBookReadModelError,
    render_manual_order_book_price_read_model,
)


EXIT_INVALID_INPUT = 2
EXIT_OPERATIONAL_ERROR = 4
_INTEGER_ARGUMENT_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")


class _CliArgumentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise _CliArgumentError("cli_arguments_invalid")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        contract_version = _integer_argument(
            args.contract_version,
            "price_contract_version_type_invalid",
        )
        level_index = (
            None
            if args.level_index is None
            else _integer_argument(
                args.level_index,
                "price_level_selector_index_type_invalid",
            )
        )
    except _CliArgumentError as exc:
        _write_error(exc.code, "invalid_input")
        return EXIT_INVALID_INPUT

    try:
        content = read_manual_order_book_file(args.file)
    except ManualOrderBookValidationError as exc:
        if exc.code in {"file_not_found", "file_read_error"}:
            _write_error(exc.code, "operational_error")
            return EXIT_OPERATIONAL_ERROR
        _write_error(exc.code, "invalid_input")
        return EXIT_INVALID_INPUT

    try:
        output_bytes = render_manual_order_book_price_read_model(
            content,
            contract_id=args.contract_id,
            contract_version=contract_version,
            display_currency=args.currency,
            side=args.side,
            level_index=level_index,
        )
    except (
        ManualOrderBookValidationError,
        ManualOrderBookReadModelError,
        PriceInterpretationError,
    ) as exc:
        _write_error(exc.code, "invalid_input")
        return EXIT_INVALID_INPUT

    sys.stdout.buffer.write(output_bytes)
    return 0


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        description=(
            "Build a versioned price read model from one explicit local "
            "confirmed capture without database or network access."
        )
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--contract-id", required=True)
    parser.add_argument("--contract-version", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--side")
    parser.add_argument("--level-index")
    return parser


def _integer_argument(value: str, code: str) -> int:
    if _INTEGER_ARGUMENT_RE.fullmatch(value) is None:
        raise _CliArgumentError(code)
    try:
        return int(value)
    except (ValueError, OverflowError):
        raise _CliArgumentError(code) from None


def _write_error(code: str, error_type: str) -> None:
    text = json.dumps(
        {"error_code": code, "error_type": error_type},
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stderr.write(text + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
