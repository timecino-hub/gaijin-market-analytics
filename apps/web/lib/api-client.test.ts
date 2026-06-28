import assert from "node:assert/strict";
import test from "node:test";
import { ApiRequestError, buildApiUrl, toDisplayError } from "./api-client.ts";

test("buildApiUrl omits empty parameters and encodes query values", () => {
  const url = buildApiUrl("/api/v1/items", {
    search: "Synthetic Alpha",
    category: "",
    page: "1"
  });

  assert.equal(url, "http://localhost:8000/api/v1/items?search=Synthetic+Alpha&page=1");
});

test("toDisplayError preserves sanitized API business errors", () => {
  const error = toDisplayError(
    new ApiRequestError({
      status: 404,
      code: "item_not_found",
      message: "The requested item was not found."
    })
  );

  assert.deepEqual(error, {
    status: 404,
    code: "item_not_found",
    message: "The requested item was not found."
  });
});

test("toDisplayError maps network failures to an API unavailable message", () => {
  const error = toDisplayError(new TypeError("fetch failed"));

  assert.equal(error.status, 0);
  assert.equal(error.code, "api_unreachable");
});
