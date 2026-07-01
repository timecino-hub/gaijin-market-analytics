from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from api.screen_recognition.contracts import (
    MARKET_PRICE_CAP,
    OcrFieldEvidence,
    OcrLineEvidence,
    OcrWordEvidence,
)


FIELD_OCR_PIPELINES = {
    "item_name": (
        "title_roi_2x_gray_autocontrast",
        "title_roi_padded_3x_gray_autocontrast",
    ),
    "price": (
        "scalar_price_roi_3x",
        "scalar_price_roi_4x_gray_autocontrast",
        "first_level_price_roi_3x",
    ),
    "quantity": (
        "summary_quantity_roi_3x",
        "summary_quantity_roi_4x_gray_autocontrast",
    ),
}

PRICE_SELECTION_ORDER = (
    "independent_roi_agreement",
    "repeated_candidate_agreement",
    "single_explicit_decimal",
    "single_integer_price",
)

_NUMERIC_CONTEXT_RE = re.compile(r"^[\s\dOoIlLl,.\u3001\uff0c\uff0e\u3002]+$")
_PRICE_TOKEN_RE = re.compile(r"\d+(?:\.\d{1,2})?\+?")
_INTEGER_TOKEN_RE = re.compile(r"\d+")
_DECIMAL_LIKE_RE = re.compile(r"\d+\s*[\.,\u3001\uff0c\uff0e\u3002]\s*\d+")
_PRICE_MARKER_RE = re.compile(r"\bGJN\b|价格|價|浠.*鏍", re.IGNORECASE)
_SUMMARY_LABELS = {
    "bid": ("正在购买", "购买", "求购", "采购"),
    "ask": ("正在出售", "出售", "售卖", "销售"),
}


@dataclass(frozen=True)
class OcrPriceCandidate:
    pipeline_id: str
    source: str
    raw_text: str
    normalized_token: str | None
    decimal_value: Decimal | None
    contains_explicit_decimal: bool
    selected: bool = False
    correction_codes: tuple[str, ...] = ()
    rejection_reason: str | None = None


@dataclass(frozen=True)
class SelectedPriceCandidate:
    value: Decimal | None
    candidates: tuple[OcrPriceCandidate, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    selection_reason: str | None = None


@dataclass(frozen=True)
class OcrQuantityCandidate:
    pipeline_id: str
    source: str
    raw_text: str
    integer_token: str | None
    value: int | None
    selected: bool = False
    label_anchored: bool = False
    rejection_reason: str | None = None


@dataclass(frozen=True)
class SelectedQuantityCandidate:
    value: int | None
    candidates: tuple[OcrQuantityCandidate, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    selection_reason: str | None = None


def normalize_numeric_ocr_token(raw: str) -> tuple[str, tuple[str, ...], bool]:
    text = unicodedata.normalize("NFKC", raw or "")
    corrections: list[str] = []
    if _NUMERIC_CONTEXT_RE.fullmatch(text):
        replaced = text.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "L": "1"}))
        if replaced != text:
            corrections.append("numeric_confusable_repaired")
        text = replaced
    if any(separator in text for separator in ("\u3001", "\uff0c", "\uff0e", "\u3002", ",")):
        corrections.append("decimal_separator_normalized")
    text = re.sub(r"(?<=\d)\s*[\u3001\uff0c\uff0e\u3002,]\s*(?=\d{1,2}\b)", ".", text)
    text = re.sub(r"(?<=\d)\s+\.\s*(?=\d{1,2}\b)", ".", text)
    contains_explicit_decimal = "." in text
    compact = re.sub(r"\s+", "", text)
    return compact, tuple(dict.fromkeys(corrections)), contains_explicit_decimal


def build_price_candidates(
    *,
    field_name: str,
    scalar_text: str,
    first_level_text: str | None = None,
) -> tuple[OcrPriceCandidate, ...]:
    candidates: list[OcrPriceCandidate] = []
    candidates.extend(
        _candidates_from_text(
            pipeline_id=f"{field_name}_scalar_price_roi",
            source="scalar_price_roi",
            text=scalar_text,
            prefer_after_price_label=False,
        )
    )
    if first_level_text:
        candidates.extend(
            _candidates_from_text(
                pipeline_id=f"{field_name}_first_level_price_roi",
                source="first_level_price_roi",
                text=first_level_text,
                prefer_after_price_label=True,
            )
        )
    return tuple(candidates)


def select_price_candidate(
    *,
    field_name: str,
    scalar_text: str,
    first_level_text: str | None = None,
) -> SelectedPriceCandidate:
    candidates = build_price_candidates(
        field_name=field_name, scalar_text=scalar_text, first_level_text=first_level_text
    )
    valid = [candidate for candidate in candidates if candidate.decimal_value is not None]
    if not valid:
        errors = [f"{field_name}_missing"]
        if any(candidate.rejection_reason for candidate in candidates):
            errors.append("price_ocr_invalid")
        return SelectedPriceCandidate(value=None, candidates=candidates, errors=tuple(errors))

    by_source: dict[str, set[Decimal]] = {}
    for candidate in valid:
        by_source.setdefault(candidate.source, set()).add(candidate.decimal_value or Decimal("0"))
    if len(by_source) >= 2:
        common = set.intersection(*by_source.values())
        if len(common) == 1:
            value = next(iter(common))
            return _selected(valid, value, "independent_roi_agreement")
        if len(set().union(*by_source.values())) > 1:
            return SelectedPriceCandidate(
                value=None,
                candidates=candidates,
                errors=("ocr_candidate_ambiguous", f"{field_name}_missing"),
            )

    counts: dict[Decimal, int] = {}
    for candidate in valid:
        assert candidate.decimal_value is not None
        counts[candidate.decimal_value] = counts.get(candidate.decimal_value, 0) + 1
    repeated = [value for value, count in counts.items() if count >= 2]
    if len(repeated) == 1:
        return _selected(valid, repeated[0], "repeated_candidate_agreement")
    if len(repeated) > 1:
        return SelectedPriceCandidate(
            value=None,
            candidates=candidates,
            errors=("ocr_candidate_ambiguous", f"{field_name}_missing"),
        )

    explicit = [candidate for candidate in valid if candidate.contains_explicit_decimal]
    if len(explicit) == 1:
        return _selected(valid, explicit[0].decimal_value, "single_explicit_decimal")
    if len(explicit) > 1:
        return SelectedPriceCandidate(
            value=None,
            candidates=candidates,
            errors=("ocr_candidate_ambiguous", f"{field_name}_missing"),
        )

    if len(valid) == 1:
        return _selected(valid, valid[0].decimal_value, "single_integer_price")
    return SelectedPriceCandidate(
        value=None,
        candidates=candidates,
        errors=("price_decimal_unconfirmed", f"{field_name}_missing"),
    )


def parse_quantity_candidate(text: str) -> tuple[int | None, tuple[str, ...]]:
    selected = select_quantity_candidate(
        field_name="total_quantity",
        compact_evidence=_text_evidence("total_quantity", text),
        summary_evidence=None,
        side="bid",
    )
    return selected.value, selected.errors


def select_quantity_candidate(
    *,
    field_name: str,
    compact_evidence: OcrFieldEvidence | None,
    summary_evidence: OcrFieldEvidence | None,
    side: str,
) -> SelectedQuantityCandidate:
    candidates: list[OcrQuantityCandidate] = []
    if compact_evidence is not None:
        candidates.extend(_compact_quantity_candidates(field_name, compact_evidence))
    summary_candidates, summary_label_detected = _summary_quantity_candidates(
        field_name=field_name,
        evidence=summary_evidence,
        side=side,
    )
    candidates.extend(summary_candidates)

    valid = [candidate for candidate in candidates if candidate.value is not None]
    if not valid:
        errors = [f"{field_name}_missing"]
        if summary_evidence is not None and (summary_evidence.raw_text or summary_evidence.lines) and not summary_label_detected:
            errors.append("quantity_label_not_detected")
        if any(candidate.rejection_reason for candidate in candidates):
            errors.append("quantity_ocr_invalid")
            if any(candidate.rejection_reason == "quantity_candidate_looks_like_price" for candidate in candidates):
                errors.append("quantity_candidate_looks_like_price")
            if any(candidate.rejection_reason == "quantity_candidate_outside_summary" for candidate in candidates):
                errors.append("quantity_candidate_outside_summary")
        else:
            errors.append("quantity_ocr_invalid")
        return SelectedQuantityCandidate(value=None, candidates=tuple(candidates), errors=tuple(dict.fromkeys(errors)))

    values = {candidate.value for candidate in valid}
    if len(values) > 1:
        return SelectedQuantityCandidate(
            value=None,
            candidates=tuple(candidates),
            errors=("quantity_candidate_ambiguous", f"{field_name}_missing"),
        )
    value = next(iter(values))
    return _selected_quantity(candidates, value, _quantity_selection_reason(valid))


def _selected(
    candidates: list[OcrPriceCandidate], value: Decimal | None, reason: str
) -> SelectedPriceCandidate:
    selected = tuple(
        OcrPriceCandidate(
            pipeline_id=candidate.pipeline_id,
            source=candidate.source,
            raw_text=candidate.raw_text,
            normalized_token=candidate.normalized_token,
            decimal_value=candidate.decimal_value,
            contains_explicit_decimal=candidate.contains_explicit_decimal,
            selected=candidate.decimal_value == value,
            correction_codes=candidate.correction_codes,
            rejection_reason=candidate.rejection_reason,
        )
        for candidate in candidates
    )
    warnings = tuple(
        code
        for candidate in selected
        if candidate.selected
        for code in candidate.correction_codes
    )
    return SelectedPriceCandidate(
        value=value, candidates=selected, warnings=warnings, selection_reason=reason
    )


def _selected_quantity(
    candidates: list[OcrQuantityCandidate], value: int | None, reason: str
) -> SelectedQuantityCandidate:
    selected = tuple(
        OcrQuantityCandidate(
            pipeline_id=candidate.pipeline_id,
            source=candidate.source,
            raw_text=candidate.raw_text,
            integer_token=candidate.integer_token,
            value=candidate.value,
            selected=candidate.value == value,
            label_anchored=candidate.label_anchored,
            rejection_reason=candidate.rejection_reason,
        )
        for candidate in candidates
    )
    return SelectedQuantityCandidate(value=value, candidates=selected, selection_reason=reason)


def _quantity_selection_reason(candidates: list[OcrQuantityCandidate]) -> str:
    sources = {candidate.source for candidate in candidates}
    if len(sources) >= 2:
        return "independent_quantity_source_agreement"
    if candidates[0].label_anchored:
        return "single_label_anchored_quantity"
    return "single_compact_quantity"


def _compact_quantity_candidates(field_name: str, evidence: OcrFieldEvidence) -> list[OcrQuantityCandidate]:
    raw_text = evidence.raw_text or ""
    normalized = unicodedata.normalize("NFKC", raw_text).strip()
    if not normalized:
        return [
            _quantity_candidate(
                field_name,
                "compact_quantity_roi",
                raw_text,
                None,
                None,
                rejection_reason="quantity_ocr_invalid",
            )
        ]
    if _looks_like_price_context(normalized):
        return [
            _quantity_candidate(
                field_name,
                "compact_quantity_roi",
                raw_text,
                None,
                None,
                rejection_reason="quantity_candidate_looks_like_price",
            )
        ]
    tokens = _INTEGER_TOKEN_RE.findall(normalized)
    if len(tokens) == 1:
        return [
            _quantity_candidate(
                field_name, "compact_quantity_roi", raw_text, tokens[0], int(tokens[0])
            )
        ]
    durable_tokens = [token for token in tokens if len(token) >= 2]
    artifact_tokens = [token for token in tokens if len(token) == 1]
    if len(set(durable_tokens)) == 1 and artifact_tokens:
        token = durable_tokens[0]
        return [
            _quantity_candidate(
                field_name, "compact_quantity_roi", raw_text, token, int(token)
            ),
            *[
                _quantity_candidate(
                    field_name,
                    "compact_quantity_roi",
                    raw_text,
                    artifact,
                    None,
                    rejection_reason="quantity_ocr_invalid",
                )
                for artifact in artifact_tokens
            ],
        ]
    return [
        _quantity_candidate(
            field_name,
            "compact_quantity_roi",
            raw_text,
            token,
            None,
            rejection_reason="quantity_ocr_invalid",
        )
        for token in tokens
    ] or [
        _quantity_candidate(
            field_name,
            "compact_quantity_roi",
            raw_text,
            None,
            None,
            rejection_reason="quantity_ocr_invalid",
        )
    ]


def _summary_quantity_candidates(
    *, field_name: str, evidence: OcrFieldEvidence | None, side: str
) -> tuple[list[OcrQuantityCandidate], bool]:
    if evidence is None or not ((evidence.raw_text or "").strip() or evidence.lines):
        return [], False
    labels = _SUMMARY_LABELS.get(side, ())
    label_lines = [
        line for line in evidence.lines if _contains_summary_label(line.text, labels)
    ]
    label_detected = bool(label_lines) or _contains_summary_label(evidence.raw_text, labels)
    if not label_detected:
        return [
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                evidence.raw_text,
                None,
                None,
                label_anchored=True,
                rejection_reason="quantity_label_not_detected",
            )
        ], False

    candidates: list[OcrQuantityCandidate] = []
    anchor = label_lines[0] if label_lines else None
    for line in evidence.lines:
        if anchor is not None and line.order < anchor.order:
            continue
        candidates.extend(_summary_line_candidates(field_name, line, anchor))
    if not candidates:
        candidates.extend(
            _summary_text_fallback_candidates(
                field_name=field_name,
                text=evidence.raw_text,
                labels=labels,
            )
        )
    return candidates, label_detected


def _summary_text_fallback_candidates(
    *, field_name: str, text: str, labels: tuple[str, ...]
) -> list[OcrQuantityCandidate]:
    normalized = unicodedata.normalize("NFKC", text or "")
    label_index = _first_label_index(normalized, labels)
    if label_index is None:
        return []
    after_label = normalized[label_index:]
    if _looks_like_price_context(after_label):
        return [
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                text,
                token,
                None,
                label_anchored=True,
                rejection_reason="quantity_candidate_looks_like_price",
            )
            for token in _INTEGER_TOKEN_RE.findall(after_label)
        ]
    tokens = _INTEGER_TOKEN_RE.findall(after_label)
    if len(tokens) == 1:
        return [
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                text,
                tokens[0],
                int(tokens[0]),
                label_anchored=True,
            )
        ]
    return [
        _quantity_candidate(
            field_name,
            "summary_label_quantity_roi",
            text,
            token,
            None,
            label_anchored=True,
            rejection_reason="quantity_ocr_invalid",
        )
        for token in tokens
    ]


def _summary_line_candidates(
    field_name: str, line: OcrLineEvidence, anchor: OcrLineEvidence | None
) -> list[OcrQuantityCandidate]:
    integer_words = [
        (word, unicodedata.normalize("NFKC", word.text or ""))
        for word in line.words
        if _INTEGER_TOKEN_RE.fullmatch(unicodedata.normalize("NFKC", word.text or ""))
    ]
    if not integer_words:
        return []
    if anchor is not None and line.order > anchor.order and _line_starts_with_non_integer_before_quantity(line):
        return [
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                line.text,
                token,
                None,
                label_anchored=True,
                rejection_reason="quantity_candidate_looks_like_price",
            )
            for _word, token in integer_words
        ]
    if _first_integer_starts_decimal(line.text):
        return [
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                line.text,
                token,
                None,
                label_anchored=True,
                rejection_reason="quantity_candidate_looks_like_price",
            )
            for _word, token in integer_words
        ]
    candidates: list[OcrQuantityCandidate] = []
    selected = False
    for word, token in integer_words:
        if anchor is not None and not _word_is_near_label(word, line, anchor):
            candidates.append(
                _quantity_candidate(
                    field_name,
                    "summary_label_quantity_roi",
                    line.text,
                    token,
                    None,
                    label_anchored=True,
                    rejection_reason="quantity_candidate_outside_summary",
                )
            )
            continue
        if not selected:
            candidates.append(
                _quantity_candidate(
                    field_name,
                    "summary_label_quantity_roi",
                    line.text,
                    token,
                    int(token),
                    label_anchored=True,
                )
            )
            selected = True
            continue
        candidates.append(
            _quantity_candidate(
                field_name,
                "summary_label_quantity_roi",
                line.text,
                token,
                None,
                label_anchored=True,
                rejection_reason="quantity_candidate_looks_like_price",
            )
        )
    return candidates


def _line_starts_with_non_integer_before_quantity(line: OcrLineEvidence) -> bool:
    for word in line.words:
        normalized = unicodedata.normalize("NFKC", word.text or "")
        if not normalized.strip():
            continue
        return _INTEGER_TOKEN_RE.fullmatch(normalized) is None
    return False


def _quantity_candidate(
    field_name: str,
    source: str,
    raw_text: str,
    token: str | None,
    value: int | None,
    *,
    label_anchored: bool = False,
    rejection_reason: str | None = None,
) -> OcrQuantityCandidate:
    return OcrQuantityCandidate(
        pipeline_id=f"{field_name}_{source}",
        source=source,
        raw_text=raw_text,
        integer_token=token,
        value=value,
        label_anchored=label_anchored,
        rejection_reason=rejection_reason,
    )


def _text_evidence(field_name: str, text: str) -> OcrFieldEvidence:
    return OcrFieldEvidence(field_name=field_name, raw_text=text or "", confidence=None)


def _looks_like_price_context(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    return bool(_PRICE_MARKER_RE.search(normalized) or _DECIMAL_LIKE_RE.search(normalized))


def _first_integer_starts_decimal(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    first_integer = _INTEGER_TOKEN_RE.search(normalized)
    if first_integer is None:
        return False
    decimal = _DECIMAL_LIKE_RE.search(normalized)
    return decimal is not None and decimal.start() <= first_integer.start() <= decimal.end()


def _contains_summary_label(text: str, labels: tuple[str, ...]) -> bool:
    normalized = _compact_cjk_spaces(unicodedata.normalize("NFKC", text or ""))
    return any(label in normalized for label in labels)


def _first_label_index(text: str, labels: tuple[str, ...]) -> int | None:
    compact = _compact_cjk_spaces(text)
    indices = [compact.find(label) for label in labels if compact.find(label) >= 0]
    return min(indices) if indices else None


def _compact_cjk_spaces(text: str) -> str:
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)


def _word_is_near_label(
    word: OcrWordEvidence, line: OcrLineEvidence, anchor: OcrLineEvidence
) -> bool:
    if word.bounding_box is None:
        return line.order in {anchor.order, anchor.order + 1}
    if line.order == anchor.order:
        return True
    if line.order == anchor.order + 1:
        return True
    return False


def _candidates_from_text(
    *, pipeline_id: str, source: str, text: str, prefer_after_price_label: bool
) -> list[OcrPriceCandidate]:
    raw_text = text or ""
    search_text = raw_text
    if prefer_after_price_label:
        match = re.search(r"GJN\)?", search_text, flags=re.IGNORECASE)
        if match:
            search_text = search_text[match.end() :]
    normalized, corrections, contains_decimal = normalize_numeric_ocr_token(search_text)
    tokens = _PRICE_TOKEN_RE.findall(normalized)
    if not tokens:
        return [
            OcrPriceCandidate(
                pipeline_id=pipeline_id,
                source=source,
                raw_text=raw_text,
                normalized_token=None,
                decimal_value=None,
                contains_explicit_decimal=False,
                correction_codes=corrections,
                rejection_reason="price_ocr_invalid",
            )
        ]
    candidates: list[OcrPriceCandidate] = []
    for token in tokens:
        token_has_decimal = "." in token
        parsed = _parse_candidate_decimal(token.rstrip("+"))
        rejection = None
        if parsed is None:
            rejection = "price_ocr_invalid"
        elif parsed == 0 and len(tokens) == 1 and normalized == token:
            rejection = None
        elif parsed <= 0 or parsed > MARKET_PRICE_CAP:
            if token_has_decimal or len(token.rstrip("+")) <= 4:
                rejection = "price_ocr_invalid"
            else:
                rejection = "price_decimal_unconfirmed"
            parsed = None
        candidates.append(
            OcrPriceCandidate(
                pipeline_id=pipeline_id,
                source=source,
                raw_text=raw_text,
                normalized_token=token,
                decimal_value=parsed,
                contains_explicit_decimal=contains_decimal or token_has_decimal,
                correction_codes=corrections,
                rejection_reason=rejection,
            )
        )
    if prefer_after_price_label:
        explicit_decimal_candidates = [
            candidate for candidate in candidates if candidate.contains_explicit_decimal
        ]
        if explicit_decimal_candidates:
            return explicit_decimal_candidates[:1]
    return candidates


def _parse_candidate_decimal(token: str) -> Decimal | None:
    try:
        parsed = Decimal(token)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return parsed
