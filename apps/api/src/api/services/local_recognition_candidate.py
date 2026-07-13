from __future__ import annotations

import hashlib
import json
from typing import Any

from api.schemas.local_recognition import ReviewedCandidate


def candidate_audit_payload(candidate: ReviewedCandidate) -> dict[str, Any]:
    immutable_candidate = candidate.model_copy(
        update={
            "imported": False,
            "database_written": False,
            "market_snapshot_created": False,
            "database_item_id": None,
            "screen_review_import_id": None,
            "market_snapshot_id": None,
            "order_book_observation_id": None,
            "imported_at": None,
        }
    )
    return immutable_candidate.model_dump(mode="json")


def candidate_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
