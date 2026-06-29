import assert from "node:assert/strict";
import test from "node:test";
import { ApiRequestError, buildApiUrl, toDisplayError, uploadCsvImport } from "./api-client.ts";

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

test("uploadCsvImport posts FormData without a manual multipart content type", async () => {
  const originalFetch = globalThis.fetch;
  let captured: { url: string; init?: RequestInit } | undefined;

  globalThis.fetch = async (url, init) => {
    captured = { url: String(url), init };
    return Response.json(
      {
        job_id: 1,
        status: "completed",
        filename: "synthetic.csv",
        checksum: "a".repeat(64),
        row_count: 1,
        valid_row_count: 1,
        invalid_row_count: 0,
        duplicate_of_job_id: null
      },
      { status: 201 }
    );
  };

  try {
    const result = await uploadCsvImport(new File(["a,b\n"], "synthetic.csv", { type: "text/csv" }));

    assert.equal(result.status, "completed");
    assert.equal(captured?.url, "http://localhost:8000/api/v1/imports/csv");
    assert.equal(captured?.init?.method, "POST");
    assert.ok(captured?.init?.body instanceof FormData);
    assert.deepEqual(captured?.init?.headers, { Accept: "application/json" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uploadCsvImport preserves stable business errors with friendly oversized file text", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () =>
    Response.json(
      {
        detail: {
          error_code: "file_too_large",
          message: "CSV uploads are limited to 10 MB."
        }
      },
      { status: 413 }
    );

  try {
    await assert.rejects(
      uploadCsvImport(new File(["x"], "large.csv", { type: "text/csv" })),
      (error) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.error.status, 413);
        assert.equal(error.error.code, "file_too_large");
        assert.match(error.error.message, /10 MB/);
        return true;
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uploadCsvImport hides sensitive server error details", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () =>
    Response.json(
      {
        detail: {
          error_code: "database_error",
          message: 'Traceback File "C:\\app\\db.py" SELECT * FROM secrets'
        }
      },
      { status: 500 }
    );

  try {
    await assert.rejects(
      uploadCsvImport(new File(["x"], "synthetic.csv", { type: "text/csv" })),
      (error) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.error.status, 500);
        assert.equal(error.error.code, "database_error");
        assert.doesNotMatch(error.error.message, /Traceback|SELECT|C:\\/);
        return true;
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
