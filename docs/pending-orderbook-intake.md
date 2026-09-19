# Pending order-book operator gate

Automatic extension exports remain `pending_user_confirmation` until an
operator reviews item identity, capture time, source summary, and book
structure. The public site stays read-only.

Use the explicit gate before either dry-run or write:

```sh
./deploy/import-approved-orderbook.sh /var/lib/gaijin-market-web-mvp/intake/order-books/capture.json --confirm-pending
./deploy/import-approved-orderbook.sh /var/lib/gaijin-market-web-mvp/intake/order-books/capture.json --confirm-pending --write
```

The gate verifies the original pending fingerprint, changes only the review
status and normalized fingerprint, and then runs the existing strict confirmed
validator. It rejects already-confirmed documents, fill claims, crossed books,
and captures that still require manual review. `--write` remains a separate,
explicit database action.

The archived pending file remains the source artifact. Its SHA-256 is stored as
the source-file checksum, while the promoted confirmed payload and its newly
computed normalized fingerprint are stored as the reviewed database document.
