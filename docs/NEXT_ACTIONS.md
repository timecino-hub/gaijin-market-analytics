# Next Actions

## Manual acceptance

Run the real local browser smoke flow against an isolated database:

1. Upload a user-selected current screenshot.
2. Confirm a candidate against an existing item.
3. Verify confirmation alone has not added a snapshot or audit row.
4. Explicitly import, then verify the linked snapshot and audit identifiers.
5. Repeat import and verify idempotency and unchanged row counts.
6. Confirm screenshot totals remain audit-only and market snapshot count fields
   remain null.

Do not upload private fixtures, screenshots, environment files, or database
dumps to source control or validation packages.

## Future product work

- Provide read-only discovery of imported review audits from item/snapshot views
  without exposing screenshots or sensitive source metadata.
- Decide whether pending review metadata should persist across API restarts;
  keep original screenshots non-persistent by default.
- Profile future OCR candidates on the same fixture manifest before comparing
  accuracy or promotion safety.
- Keep marketplace automation, unattended capture, cookies, scraping, and
  trading permanently out of scope.
