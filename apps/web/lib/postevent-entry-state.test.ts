import assert from "node:assert/strict";
import test from "node:test";
import { postEventEntryStateFromParams } from "./postevent-entry-state.ts";

test("defaults show every vehicle, active only, at the current instant", () => {
  const state = postEventEntryStateFromParams({});

  assert.equal(state.query.candidates_only, "false");
  assert.equal(state.query.include_inactive, "false");
  assert.equal(state.query.as_of, undefined);
  assert.deepEqual(state.form, { asOf: "", candidatesOnly: false, includeInactive: false });
});

test("booleans only accept the two literal values", () => {
  assert.equal(
    postEventEntryStateFromParams({ candidates_only: "true" }).query.candidates_only,
    "true"
  );
  assert.equal(
    postEventEntryStateFromParams({ candidates_only: "yes" }).query.candidates_only,
    "false"
  );
  assert.equal(
    postEventEntryStateFromParams({ include_inactive: "true" }).query.include_inactive,
    "true"
  );
});

test("as_of is trimmed, passed through, and dropped when absurdly long", () => {
  assert.equal(
    postEventEntryStateFromParams({ as_of: "  2026-05-07T00:00:00Z  " }).query.as_of,
    "2026-05-07T00:00:00Z"
  );
  assert.equal(postEventEntryStateFromParams({ as_of: "x".repeat(81) }).query.as_of, undefined);
  assert.equal(postEventEntryStateFromParams({ as_of: "   " }).query.as_of, undefined);
});

test("a repeated parameter uses the first value rather than an array", () => {
  const state = postEventEntryStateFromParams({ candidates_only: ["true", "false"] });

  assert.equal(state.query.candidates_only, "true");
});
