# Local Extension Bridge

The Local Extension Bridge lets a future browser extension submit a user-visible
PNG/JPEG screenshot into the existing local screen-recognition review workflow.
This repository does not implement the extension itself.

## Scope

Implemented in this repository:

- in-memory pairing codes
- in-memory extension pairings
- authenticated extension screenshot upload
- per-pairing upload rate limiting
- short-window screenshot deduplication
- source metadata on local reviews
- review-page controls for pairing and revocation

Not implemented here:

- browser extension source, manifest, popup, or options
- Gaijin Market page matching
- automatic screenshots
- automatic browsing or DOM access
- cookies, request headers, localStorage, sessionStorage, or browsing history
- CSV generation
- market snapshot writes
- database migrations
- trading actions

## Pairing

The management page creates a one-time pairing code:

```text
POST /api/v1/local-recognition/pairing-codes
```

The response includes `pairing_code_id`, `pairing_code`, `expires_at`, and
`ttl_seconds`. The code is 12 characters from an unambiguous Crockford Base32
alphabet and carries about 60 bits of entropy. It is grouped for display, for
example `ABCD-EFGH-JK12`.

The extension later submits:

```text
POST /api/v1/local-recognition/pair
```

with `pairing_code_id`, `pairing_code`, `client_name`, and
`extension_version`. A successful response returns `pairing_id`, `token`, and
`created_at`. The token is returned only once.

The API process stores only HMAC-SHA256 digests of pairing codes and tokens.
The HMAC process secret is generated at API startup and is not persisted.
Restarting the API loses all pairings and requires pairing again.

Pairing abuse protection:

- pairing code TTL: 10 minutes
- maximum failed attempts per code: 5
- per-client pairing attempts: 10/min
- global pairing attempts: 30/min
- consumed, expired, and attempts-exceeded codes cannot be reused

## Management Protection

The management endpoints are:

```text
POST   /api/v1/local-recognition/pairing-codes
GET    /api/v1/local-recognition/extension-status
DELETE /api/v1/local-recognition/pairings/{pairing_id}
```

They require an allowed `Origin` header derived from the configured local Web
CORS origins. By default `http://localhost:3000` is allowed, and the equivalent
`http://127.0.0.1:3000` origin is also accepted.

Missing or unconfigured origins are rejected with stable error codes. This
protection is intended to reduce ordinary malicious web pages making cross-site
requests to localhost. It does not claim to defend against a malicious process
that already controls the local machine.

## Extension Upload

The extension uploads screenshots to:

```text
POST /api/v1/local-recognition/extension-reviews
Authorization: Bearer <token>
```

The upload path reuses the same local review creation flow as manual uploads:

```text
validate image
-> create processing Review
-> run Windows OCR background task
-> pending_review / unreadable / failed
-> existing confirm/reject/unreadable/candidate flow
```

Image limits are unchanged:

- maximum image content: 10 MB
- maximum decoded pixels: 40,000,000
- PNG/JPEG only
- extension and magic bytes must match
- Pillow safe decode is required
- original temp image is deleted in the OCR `finally` path
- debug crops, overlays, and source images are not stored

Uploads are rate-limited per pairing with a token bucket:

```text
capacity = 3
refill = 6/min
```

Rate limit responses use HTTP 429 with `extension_rate_limited` and a dynamic
`retry_after_seconds`.

Screenshot deduplication uses:

```text
pairing_id + capture_sha256
window = 20 seconds
```

Duplicates are idempotent successes. They return HTTP 200 with
`deduplicated=true` and the existing `review_id`; they do not run OCR again.
Different pairings do not deduplicate each other.

## Source Privacy

Manual reviews use:

```text
source = manual_upload
```

Extension reviews use:

```text
source = browser_extension
```

Extension metadata can include `extension_version`, `source_url_safe`,
`source_tab_title`, `capture_sha256`, and `pairing_id`.

The raw source URL is untrusted. Before storing, the API removes username,
password, query, and fragment. Only scheme, hostname, port, and pathname are
kept. Logs must not include raw URLs, pathnames, query strings, fragments,
tokens, token hashes, pairing codes, pairing code hashes, cookies, or request
headers.

The reviewed candidate JSON remains minimal and does not include pairing id,
source URL, tab title, or extension client name.

## Loopback and Docker

The local API defaults to binding `127.0.0.1` when run directly through
`api.main`. The current Docker Compose file only runs PostgreSQL. If an API
container is added later, it may need to listen on `0.0.0.0` inside the
container, but the host port must be published to loopback, for example:

```text
127.0.0.1:8000:8000
```

The extension upload endpoint also rejects non-loopback clients in the current
direct-host mode and never trusts `X-Forwarded-For`.
