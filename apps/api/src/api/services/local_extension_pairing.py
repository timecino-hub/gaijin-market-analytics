from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta


CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIRING_CODE_LENGTH = 12
PAIRING_CODE_TTL_SECONDS = 600
PAIRING_CODE_MAX_FAILED_ATTEMPTS = 5
PAIR_ATTEMPTS_PER_CLIENT_PER_MINUTE = 10
GLOBAL_PAIR_ATTEMPTS_PER_MINUTE = 30
MAX_ACTIVE_PAIRING_CODES = 10
MAX_ACTIVE_PAIRINGS = 10
UPLOAD_RATE_LIMIT_CAPACITY = 3
UPLOAD_RATE_LIMIT_REFILL_PER_MINUTE = 6
CAPTURE_DEDUP_WINDOW_SECONDS = 20


class PairingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RateLimitExceeded(PairingError):
    def __init__(self, code: str, message: str, retry_after_seconds: int) -> None:
        super().__init__(code, message)
        self.retry_after_seconds = retry_after_seconds


class CaptureAlreadyReserved(PairingError):
    pass


@dataclass(frozen=True)
class PairingCodeRecord:
    pairing_code_id: str
    code_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    failed_attempts: int = 0
    invalidated_at: datetime | None = None


@dataclass(frozen=True)
class PairingRecord:
    pairing_id: str
    token_hash: str
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    extension_version: str | None
    client_name: str | None


@dataclass
class TokenBucket:
    capacity: int
    refill_per_second: float
    tokens: float
    updated_at: float

    def consume(self, now: float) -> int | None:
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return None
        missing = 1 - self.tokens
        return max(1, int((missing / self.refill_per_second) + 0.999))


@dataclass
class CaptureReservation:
    pairing_id: str
    capture_sha256: str
    expires_at: datetime
    review_id: str | None = None


@dataclass(frozen=True)
class PairingCodeCreated:
    pairing_code_id: str
    pairing_code: str
    expires_at: datetime
    ttl_seconds: int


@dataclass(frozen=True)
class PairingCreated:
    pairing_id: str
    token: str
    created_at: datetime


@dataclass(frozen=True)
class CaptureReserveResult:
    reserved: bool
    review_id: str | None


class LocalExtensionPairingStore:
    def __init__(
        self,
        *,
        pairing_code_ttl_seconds: int = PAIRING_CODE_TTL_SECONDS,
        max_pairing_codes: int = MAX_ACTIVE_PAIRING_CODES,
        max_pairings: int = MAX_ACTIVE_PAIRINGS,
        process_secret: bytes | None = None,
    ) -> None:
        self.pairing_code_ttl_seconds = pairing_code_ttl_seconds
        self.max_pairing_codes = max_pairing_codes
        self.max_pairings = max_pairings
        self._process_secret = process_secret or secrets.token_bytes(32)
        self._pairing_codes: dict[str, PairingCodeRecord] = {}
        self._pairings: dict[str, PairingRecord] = {}
        self._pair_attempt_buckets: dict[str, TokenBucket] = {}
        self._upload_buckets: dict[str, TokenBucket] = {}
        self._captures: dict[tuple[str, str], CaptureReservation] = {}
        self._condition = threading.Condition(threading.RLock())

    def create_pairing_code(self, *, now: datetime | None = None) -> PairingCodeCreated:
        now = now or datetime.now(UTC)
        with self._condition:
            self._cleanup_locked(now)
            active_codes = [
                code
                for code in self._pairing_codes.values()
                if code.consumed_at is None and code.invalidated_at is None and now < code.expires_at
            ]
            if len(active_codes) >= self.max_pairing_codes:
                raise PairingError("pairing_store_full", "The local pairing code store is full.")
            pairing_code = _generate_pairing_code()
            record = PairingCodeRecord(
                pairing_code_id=f"pc_{uuid.uuid4().hex}",
                code_hash=self._digest(_normalize_pairing_code(pairing_code)),
                created_at=now,
                expires_at=now + timedelta(seconds=self.pairing_code_ttl_seconds),
            )
            self._pairing_codes[record.pairing_code_id] = record
            return PairingCodeCreated(
                pairing_code_id=record.pairing_code_id,
                pairing_code=_format_pairing_code(pairing_code),
                expires_at=record.expires_at,
                ttl_seconds=self.pairing_code_ttl_seconds,
            )

    def pair(
        self,
        *,
        pairing_code_id: str,
        pairing_code: str,
        client_name: str | None,
        extension_version: str | None,
        client_key: str,
        now: datetime | None = None,
    ) -> PairingCreated:
        now = now or datetime.now(UTC)
        with self._condition:
            self._cleanup_locked(now)
            self._consume_pair_attempt_locked(f"client:{client_key}")
            self._consume_pair_attempt_locked("global")
            record = self._pairing_codes.get(pairing_code_id)
            if record is None:
                raise PairingError("pairing_code_invalid", "Pairing code is invalid.")
            if record.invalidated_at is not None:
                raise PairingError("pairing_code_attempts_exceeded", "Pairing code is no longer valid.")
            if record.consumed_at is not None:
                raise PairingError("pairing_code_consumed", "Pairing code is already consumed.")
            if now >= record.expires_at:
                raise PairingError("pairing_code_expired", "Pairing code is expired.")
            submitted_hash = self._digest(_normalize_pairing_code(pairing_code))
            if not hmac.compare_digest(submitted_hash, record.code_hash):
                failed_attempts = record.failed_attempts + 1
                invalidated_at = now if failed_attempts >= PAIRING_CODE_MAX_FAILED_ATTEMPTS else None
                self._pairing_codes[pairing_code_id] = replace(
                    record,
                    failed_attempts=failed_attempts,
                    invalidated_at=invalidated_at,
                )
                if invalidated_at is not None:
                    raise PairingError(
                        "pairing_code_attempts_exceeded",
                        "Pairing code is no longer valid.",
                    )
                raise PairingError("pairing_code_invalid", "Pairing code is invalid.")
            active_pairings = [
                pairing for pairing in self._pairings.values() if pairing.revoked_at is None
            ]
            if len(active_pairings) >= self.max_pairings:
                raise PairingError("pairing_store_full", "The local pairing store is full.")
            token = secrets.token_urlsafe(32)
            pairing = PairingRecord(
                pairing_id=f"pair_{uuid.uuid4().hex}",
                token_hash=self._digest(token),
                created_at=now,
                last_seen_at=now,
                revoked_at=None,
                extension_version=extension_version,
                client_name=client_name,
            )
            self._pairings[pairing.pairing_id] = pairing
            self._pairing_codes[pairing_code_id] = replace(record, consumed_at=now)
            return PairingCreated(pairing_id=pairing.pairing_id, token=token, created_at=now)

    def authenticate_token(self, token: str, *, now: datetime | None = None) -> PairingRecord:
        now = now or datetime.now(UTC)
        token_hash = self._digest(token)
        with self._condition:
            self._cleanup_locked(now)
            for pairing in self._pairings.values():
                if hmac.compare_digest(token_hash, pairing.token_hash):
                    if pairing.revoked_at is not None:
                        raise PairingError("extension_token_revoked", "Extension token has been revoked.")
                    updated = replace(pairing, last_seen_at=now)
                    self._pairings[pairing.pairing_id] = updated
                    return updated
        raise PairingError("extension_token_invalid", "Extension token is invalid.")

    def consume_upload_token(self, pairing_id: str, *, now_monotonic: float | None = None) -> None:
        now_monotonic = now_monotonic or time.monotonic()
        with self._condition:
            bucket = self._upload_buckets.get(pairing_id)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=UPLOAD_RATE_LIMIT_CAPACITY,
                    refill_per_second=UPLOAD_RATE_LIMIT_REFILL_PER_MINUTE / 60,
                    tokens=UPLOAD_RATE_LIMIT_CAPACITY,
                    updated_at=now_monotonic,
                )
                self._upload_buckets[pairing_id] = bucket
            retry_after = bucket.consume(now_monotonic)
            if retry_after is not None:
                raise RateLimitExceeded(
                    "extension_rate_limited",
                    "Upload rate limit exceeded.",
                    retry_after,
                )

    def reserve_capture(
        self,
        *,
        pairing_id: str,
        capture_sha256: str,
        now: datetime | None = None,
    ) -> CaptureReserveResult:
        now = now or datetime.now(UTC)
        key = (pairing_id, capture_sha256)
        with self._condition:
            self._cleanup_locked(now)
            existing = self._captures.get(key)
            if existing is not None and now < existing.expires_at:
                if existing.review_id is not None:
                    return CaptureReserveResult(reserved=False, review_id=existing.review_id)
                raise CaptureAlreadyReserved("capture_pending", "Capture is already being processed.")
            self._captures[key] = CaptureReservation(
                pairing_id=pairing_id,
                capture_sha256=capture_sha256,
                expires_at=now + timedelta(seconds=CAPTURE_DEDUP_WINDOW_SECONDS),
            )
            return CaptureReserveResult(reserved=True, review_id=None)

    def bind_capture(self, *, pairing_id: str, capture_sha256: str, review_id: str) -> None:
        key = (pairing_id, capture_sha256)
        with self._condition:
            existing = self._captures.get(key)
            if existing is not None:
                existing.review_id = review_id
                self._condition.notify_all()

    def rollback_capture(self, *, pairing_id: str, capture_sha256: str) -> None:
        with self._condition:
            self._captures.pop((pairing_id, capture_sha256), None)
            self._condition.notify_all()

    def wait_for_capture_review(
        self,
        *,
        pairing_id: str,
        capture_sha256: str,
        timeout_seconds: float = 2.0,
    ) -> str | None:
        key = (pairing_id, capture_sha256)
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                existing = self._captures.get(key)
                if existing is None:
                    return None
                if existing.review_id is not None:
                    return existing.review_id
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def revoke_pairing(self, pairing_id: str, *, now: datetime | None = None) -> PairingRecord:
        now = now or datetime.now(UTC)
        with self._condition:
            record = self._pairings.get(pairing_id)
            if record is None:
                raise PairingError("pairing_not_found", "Pairing was not found.")
            updated = replace(record, revoked_at=record.revoked_at or now)
            self._pairings[pairing_id] = updated
            return updated

    def list_pairings(self, *, now: datetime | None = None) -> list[PairingRecord]:
        now = now or datetime.now(UTC)
        with self._condition:
            self._cleanup_locked(now)
            return list(self._pairings.values())

    def clear(self) -> None:
        with self._condition:
            self._pairing_codes.clear()
            self._pairings.clear()
            self._pair_attempt_buckets.clear()
            self._upload_buckets.clear()
            self._captures.clear()
            self._condition.notify_all()

    def token_hashes_for_testing(self) -> list[str]:
        with self._condition:
            return [pairing.token_hash for pairing in self._pairings.values()]

    def _consume_pair_attempt_locked(self, key: str) -> None:
        now_monotonic = time.monotonic()
        capacity = (
            GLOBAL_PAIR_ATTEMPTS_PER_MINUTE
            if key == "global"
            else PAIR_ATTEMPTS_PER_CLIENT_PER_MINUTE
        )
        bucket = self._pair_attempt_buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=capacity,
                refill_per_second=capacity / 60,
                tokens=capacity,
                updated_at=now_monotonic,
            )
            self._pair_attempt_buckets[key] = bucket
        retry_after = bucket.consume(now_monotonic)
        if retry_after is not None:
            raise RateLimitExceeded(
                "pairing_code_invalid",
                "Pairing attempts are temporarily limited.",
                retry_after,
            )

    def _cleanup_locked(self, now: datetime) -> None:
        for key, capture in list(self._captures.items()):
            if now >= capture.expires_at:
                self._captures.pop(key, None)

    def _digest(self, value: str) -> str:
        return hmac.new(self._process_secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def capture_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _generate_pairing_code() -> str:
    return "".join(secrets.choice(CROCKFORD_BASE32) for _ in range(PAIRING_CODE_LENGTH))


def _format_pairing_code(value: str) -> str:
    return "-".join(value[index : index + 4] for index in range(0, len(value), 4))


def _normalize_pairing_code(value: str) -> str:
    normalized = value.replace("-", "").replace(" ", "").upper()
    if len(normalized) != PAIRING_CODE_LENGTH or any(char not in CROCKFORD_BASE32 for char in normalized):
        return normalized
    return normalized


pairing_store = LocalExtensionPairingStore()
