from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from api.screen_recognition.contracts import MARKET_PRICE_CAP, OcrFieldEvidence, PriceLevel, ScreenContract
from api.screen_recognition.ocr_candidates import (
    PRICE_SELECTION_POLICY_STRICT,
    select_price_candidate,
    select_quantity_candidate,
)


PRICE_RE = re.compile(r"(?P<price>\d+(?:[\.,]\d{1,2})?)(?P<plus>\+)?")
LINE_RE = re.compile(r"(?P<price>\d+(?:[\.,]\d{1,2})?\+?)\D+(?P<quantity>\d+)")


class ParseIssue(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_item_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[‐‑‒–—―]", "-", normalized)
    normalized = normalized.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s+(?=\()", "", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    normalized = re.sub(r"\(\s+", "(", normalized)
    normalized = re.sub(r"\s+\)", ")", normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=\))", "", normalized)
    normalized = re.sub(r"(?<=\()\s+(?=[\u4e00-\u9fff])", "", normalized)
    return normalized


def normalize_item_title_ocr(value: str) -> str:
    """Normalize title-bar OCR without consulting a market catalogue.

    This intentionally stays narrower than an item-name autocorrector.  The
    transformations below are deterministic OCR/UI cleanup rules observed in the
    title bar: punctuation width, slash spacing, common bracket glyphs, and a few
    tightly scoped glyph confusions inside model-style tokens.  The caller still
    marks item-title evidence for Review, so these normalizations improve
    matching and display quality without making item names trusted.
    """

    normalized = normalize_item_name(value)
    normalized = re.sub(r"^\W*War\s+Thunder\s+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^[|:;·•,，、\-\s]+", "", normalized)
    normalized = re.sub(r"[|:;·•,，、\s]+$", "", normalized)

    # Bracket glyphs frequently drift in Windows OCR for title text.
    normalized = normalized.replace("〗", ")").replace("】", ")").replace("］", ")")
    normalized = normalized.replace("〖", "(").replace("【", "(").replace("［", "(")

    # Remove spaces around model separators without joining ordinary words.
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    normalized = normalize_item_name(normalized)

    # Country-only OCR means the title prefix was not read.  Treat it as
    # missing rather than surfacing a misleading title candidate.
    if re.fullmatch(r"\((?:中国|德国|法国|美国|英国|苏联|瑞典|日本|意大利|以色列)\)", normalized):
        return ""

    # Windows OCR often reads the Latin ``z`` in Sd.Kfz. as the CJK glyph 乙.
    normalized = re.sub(r"\bSd\.Kf\s*[乙Zz]\.?(?=\s*\d)", "Sd.Kfz.", normalized)

    # Join spaced short Latin variant markers such as ``M k 88`` -> ``Mk 88``.
    normalized = re.sub(r"\bM\s+k(?=\s*\d)", "Mk", normalized)
    normalized = re.sub(r"\b[ÉÈÊË]LC(?=\s*\d)", "ELC", normalized)

    # A short horizontal dash is often recognized as the CJK one character.
    normalized = re.sub(r"(?<=[A-Za-z\u4e00-\u9fff])\s*[一—–-]\s*(?=\d)", "-", normalized)
    normalized = re.sub(r"(?<=\d)\s*[一—–-]\s*(?=[A-Za-z])", "-", normalized)

    # Constrain I/O -> 1/0 to the model-token position used in T-10A style names.
    normalized = re.sub(r"\bT-IO(?=[A-Z]\b)", "T-10", normalized)

    # ``°C yborg`` / ``℃ yborg`` is an OCR split of the quoted Cyborg word.
    normalized = re.sub(
        r"\bEF-2000\s*(?:°\s*C|℃)\s*yborg\s+Tiger[ur]?\b",
        "EF-2000 'Cyborg Tiger'",
        normalized,
        flags=re.IGNORECASE,
    )

    # Narrow cleanup for the Chinese decal title where Windows OCR breaks the
    # leading glyph and the quoted call-sign text into punctuation fragments.
    normalized = re.sub(r"^负料头像", "资料头像", normalized)
    normalized = re.sub(
        r'^资料头像\s*一\s*"\s*呼号\s*[\'"]?\s*[-一]\s*女\s*"\s*"?$',
        '资料头像--"呼号"雪女""',
        normalized,
    )

    return normalize_item_name(normalized)


def parse_ocr_contract(
    fields: dict[str, OcrFieldEvidence],
    *,
    item_key: str | None,
    price_selection_policy: str = PRICE_SELECTION_POLICY_STRICT,
) -> tuple[ScreenContract, list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    raw = {name: evidence.raw_text for name, evidence in fields.items()}
    item_name = normalize_item_title_ocr(raw.get("item_name", ""))
    if not item_name:
        errors.append("item_name_missing")
        errors.append("item_name_ocr_empty")
        item_name = None

    best_bid = _parse_price_field(
        raw.get("best_bid", ""),
        raw.get("bid_levels", ""),
        "best_bid",
        errors,
        warnings,
        price_selection_policy,
    )
    best_ask = _parse_price_field(
        raw.get("best_ask", ""),
        raw.get("ask_levels", ""),
        "best_ask",
        errors,
        warnings,
        price_selection_policy,
    )
    total_bid_quantity = _parse_quantity_field(
        fields.get("total_bid_quantity"),
        fields.get("total_bid_quantity_summary"),
        "total_bid_quantity",
        "bid",
        errors,
    )
    total_ask_quantity = _parse_quantity_field(
        fields.get("total_ask_quantity"),
        fields.get("total_ask_quantity_summary"),
        "total_ask_quantity",
        "ask",
        errors,
    )
    bid_levels = _parse_levels(raw.get("bid_levels", ""), errors, warnings)
    ask_levels = _parse_levels(raw.get("ask_levels", ""), errors, warnings)

    contract = ScreenContract(
        item_key=item_key,
        item_key_source="ground_truth_manifest" if item_key else None,
        item_name=item_name,
        best_bid=best_bid,
        best_ask=best_ask,
        total_bid_quantity=total_bid_quantity,
        total_ask_quantity=total_ask_quantity,
        bid_levels=tuple(bid_levels),
        ask_levels=tuple(ask_levels),
        raw_fields=raw,
    )
    errors.extend(validate_screen_contract(contract))
    return contract, warnings, errors


def validate_screen_contract(contract: ScreenContract) -> list[str]:
    errors: list[str] = []
    if contract.best_bid is None:
        errors.append("best_bid_missing")
    if contract.best_ask is None:
        errors.append("best_ask_missing")
    if contract.best_bid is not None and contract.best_ask is not None and contract.best_bid > contract.best_ask:
        errors.append("bid_ask_swapped")
    errors.extend(_validate_prices([contract.best_bid, contract.best_ask]))
    errors.extend(_validate_levels(contract.bid_levels, side="bid", best_price=contract.best_bid))
    errors.extend(_validate_levels(contract.ask_levels, side="ask", best_price=contract.best_ask))
    return errors


def _parse_price_field(
    text: str,
    first_level_text: str,
    field: str,
    errors: list[str],
    warnings: list[str],
    selection_policy: str,
) -> Decimal | None:
    selected = select_price_candidate(
        field_name=field,
        scalar_text=text,
        first_level_text=first_level_text,
        selection_policy=selection_policy,
    )
    warnings.extend(selected.warnings)
    errors.extend(selected.errors)
    return selected.value


def _parse_quantity_field(
    compact_evidence: OcrFieldEvidence | None,
    summary_evidence: OcrFieldEvidence | None,
    field: str,
    side: str,
    errors: list[str],
) -> int | None:
    selected = select_quantity_candidate(
        field_name=field,
        compact_evidence=compact_evidence,
        summary_evidence=summary_evidence,
        side=side,
    )
    errors.extend(selected.errors)
    if selected.value is None:
        errors.append(f"{field}_mismatch")
    return selected.value


def _parse_levels(text: str, errors: list[str], warnings: list[str]) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LINE_RE.search(line)
        if match:
            level = _parse_price_token(match.group("price"), warnings)
            if level is None:
                continue
            levels.append(
                PriceLevel(
                    exact_price=level.exact_price,
                    price_lower_bound=level.price_lower_bound,
                    lower_bound_inclusive=level.lower_bound_inclusive,
                    aggregation_type=level.aggregation_type,
                    quantity=int(match.group("quantity")),
                    raw_display_price=level.raw_display_price,
                    raw_quantity=match.group("quantity"),
                )
            )
            continue
        price_match = PRICE_RE.search(line)
        if price_match:
            level = _parse_price_token(
                price_match.group("price") + (price_match.group("plus") or ""), warnings
            )
            if level is not None:
                levels.append(level)
    for level in levels:
        if level.exact_price is not None:
            _validate_price(level.exact_price, errors)
        if level.price_lower_bound is not None:
            _validate_price(level.price_lower_bound, errors)
    return levels


def _parse_price_token(token: str, warnings: list[str]) -> PriceLevel | None:
    raw = token.strip()
    aggregate = raw.endswith("+")
    number = raw[:-1] if aggregate else raw
    if "," in number and "." not in number:
        number = number.replace(",", ".")
        warnings.append("decimal_separator_repaired")
    elif "," in number:
        warnings.append("decimal_separator_ambiguous")
        return None
    try:
        parsed = Decimal(number)
    except InvalidOperation:
        return None
    if aggregate:
        return PriceLevel(
            exact_price=None,
            price_lower_bound=parsed,
            lower_bound_inclusive=True,
            aggregation_type="greater_than_or_equal",
            quantity=None,
            raw_display_price=raw,
        )
    return PriceLevel(exact_price=parsed, quantity=None, raw_display_price=raw)


def _validate_prices(values: list[Decimal | None]) -> list[str]:
    errors: list[str] = []
    for value in values:
        _validate_price(value, errors)
    return errors


def _validate_price(value: Decimal | None, errors: list[str]) -> None:
    if value is None:
        return
    if value < Decimal("0.01"):
        errors.append("non_positive_price")
    if value > MARKET_PRICE_CAP:
        errors.append("price_above_market_cap")


def _validate_levels(
    levels: tuple[PriceLevel, ...], *, side: str, best_price: Decimal | None
) -> list[str]:
    errors: list[str] = []
    exact_prices = [level.exact_price for level in levels if level.exact_price is not None]
    if len(exact_prices) >= 2:
        if side == "bid" and exact_prices != sorted(exact_prices, reverse=True):
            errors.append("bid_levels_not_descending")
        if side == "ask" and exact_prices != sorted(exact_prices):
            errors.append("ask_levels_not_ascending")
    return errors


def _quantity_sum(levels: tuple[PriceLevel, ...]) -> int | None:
    if not levels or any(level.quantity is None for level in levels):
        return None
    return sum(level.quantity or 0 for level in levels)
