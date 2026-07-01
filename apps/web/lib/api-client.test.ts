import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiRequestError,
  buildApiUrl,
  confirmLocalRecognitionReview,
  getItemAnalysis,
  getLocalRecognitionReview,
  patchLocalRecognitionReview,
  toDisplayError,
  uploadLocalRecognitionReview,
  uploadCsvImport
} from "./api-client.ts";

test("buildApiUrl omits empty parameters and encodes query values", () => {
  const url = buildApiUrl("/api/v1/items", {
    search: "Synthetic Alpha",
    category: "",
    page: "1"
  });

  assert.equal(url, "http://localhost:8000/api/v1/items?search=Synthetic+Alpha&page=1");
});

test("getItemAnalysis constructs a 7 day request with no-store caching", async () => {
  const originalFetch = globalThis.fetch;
  let captured: { url: string; init?: RequestInit } | undefined;

  globalThis.fetch = async (url, init) => {
    captured = { url: String(url), init };
    return Response.json(analysisResponse({ horizon: 7 }));
  };

  try {
    await getItemAnalysis("123", { horizon: "7" });

    assert.equal(
      captured?.url,
      "http://localhost:8000/api/v1/items/123/analysis?horizon=7"
    );
    assert.equal(captured?.init?.cache, "no-store");
    assert.deepEqual(captured?.init?.headers, { Accept: "application/json" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("getItemAnalysis supports 30, 90, and 180 day requests", async () => {
  const originalFetch = globalThis.fetch;
  const capturedUrls: string[] = [];

  globalThis.fetch = async (url) => {
    capturedUrls.push(String(url));
    return Response.json(analysisResponse({ horizon: 30 }));
  };

  try {
    await getItemAnalysis("1", { horizon: "30" });
    await getItemAnalysis("1", { horizon: "90" });
    await getItemAnalysis("1", { horizon: "180" });

    assert.match(capturedUrls[0], /horizon=30/);
    assert.match(capturedUrls[1], /horizon=90/);
    assert.match(capturedUrls[2], /horizon=180/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("getItemAnalysis encodes as_of and never sends fee_rate", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";

  globalThis.fetch = async (url) => {
    capturedUrl = String(url);
    return Response.json(analysisResponse({ horizon: 30 }));
  };

  try {
    await getItemAnalysis("1", {
      horizon: "30",
      as_of: "2026-06-29T08:00:00+08:00"
    });

    assert.doesNotMatch(capturedUrl, /fee_rate/);
    assert.match(capturedUrl, /as_of=2026-06-29T08%3A00%3A00%2B08%3A00/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("getItemAnalysis omits missing as_of instead of sending an empty value", async () => {
  const originalFetch = globalThis.fetch;
  let capturedUrl = "";

  globalThis.fetch = async (url) => {
    capturedUrl = String(url);
    return Response.json(analysisResponse({ horizon: 30 }));
  };

  try {
    await getItemAnalysis("1", { horizon: "30" });

    assert.doesNotMatch(capturedUrl, /as_of=/);
    assert.doesNotMatch(capturedUrl, /fee_rate=/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("getItemAnalysis forwards AbortSignal for stale request cancellation", async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  let capturedSignal: AbortSignal | null | undefined;

  globalThis.fetch = async (_url, init) => {
    capturedSignal = init?.signal;
    return Response.json(analysisResponse({ horizon: 30 }));
  };

  try {
    await getItemAnalysis("1", { horizon: "30" }, controller.signal);

    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("getItemAnalysis preserves stable API business errors", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () =>
    Response.json(
      {
        detail: {
          code: "fee_rate_not_configurable",
          message:
            "Gaijin Market uses a fixed 15% fee with seller proceeds rounded down to 0.01 GJN."
        }
      },
      { status: 400 }
    );

  try {
    await assert.rejects(getItemAnalysis("1", { horizon: "30" }), (error) => {
      assert.ok(error instanceof ApiRequestError);
      assert.equal(error.error.status, 400);
      assert.equal(error.error.code, "fee_rate_not_configurable");
      assert.match(error.error.message, /15%/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("ItemAnalysisResponse decimal fields remain strings or null at runtime", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () =>
    Response.json(
      analysisResponse({
        horizon: 30,
        currentAsk: "12.340000",
        currentBid: null
      })
    );

  try {
    const response = await getItemAnalysis("1", { horizon: "30" });

    assert.equal(response.effective_inputs.fee_policy.nominal_fee_rate, "0.15");
    assert.equal(response.effective_inputs.fee_policy.currency_quantum, "0.01");
    assert.ok(response.effective_inputs.market_rules);
    assert.equal(response.effective_inputs.market_rules.maximum_listing_price, "2000.00");
    assert.equal(response.effective_inputs.market_rules.maximum_sale_proceeds, "1700.00");
    assert.equal(typeof response.current_ask, "string");
    assert.equal(typeof response.sale_proceeds, "string");
    assert.equal(typeof response.fee_amount, "string");
    assert.equal(typeof response.maximum_net_profit, "string");
    assert.equal(typeof response.break_even_reachable, "boolean");
    assert.equal(response.current_bid, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
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

test("uploadLocalRecognitionReview posts screenshot FormData to local review API", async () => {
  const originalFetch = globalThis.fetch;
  let captured: { url: string; init?: RequestInit } | undefined;

  globalThis.fetch = async (url, init) => {
    captured = { url: String(url), init };
    return Response.json(
      {
        review_id: "review_1",
        status: "processing",
        created_at: "2026-07-01T00:00:00Z",
        expires_at: "2026-07-01T02:00:00Z"
      },
      { status: 202 }
    );
  };

  try {
    const result = await uploadLocalRecognitionReview(
      new File(["png"], "current.png", { type: "image/png" })
    );

    assert.equal(result.status, "processing");
    assert.equal(captured?.url, "http://localhost:8000/api/v1/local-recognition/reviews");
    assert.equal(captured?.init?.method, "POST");
    assert.ok(captured?.init?.body instanceof FormData);
    assert.deepEqual(captured?.init?.headers, { Accept: "application/json" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("local recognition client supports review detail patch and confirm", async () => {
  const originalFetch = globalThis.fetch;
  const captured: Array<{ url: string; init?: RequestInit }> = [];

  globalThis.fetch = async (url, init) => {
    captured.push({ url: String(url), init });
    return Response.json(localReviewResponse());
  };

  try {
    await getLocalRecognitionReview("review/a");
    await patchLocalRecognitionReview("review/a", { final_best_bid: "12.34" });
    await confirmLocalRecognitionReview("review/a", {
      item_key: "manual-key",
      final_item_name: "Manual Name"
    });

    assert.equal(
      captured[0].url,
      "http://localhost:8000/api/v1/local-recognition/reviews/review%2Fa"
    );
    assert.equal(captured[1].init?.method, "PATCH");
    assert.equal(captured[1].init?.body, JSON.stringify({ final_best_bid: "12.34" }));
    assert.equal(captured[2].init?.method, "POST");
    assert.match(captured[2].url, /\/confirm$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("local recognition errors map to safe user messages", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = async () =>
    Response.json(
      {
        detail: {
          code: "item_identity_required",
          message: "A reviewed item identity is required before confirmation."
        }
      },
      { status: 400 }
    );

  try {
    await assert.rejects(
      confirmLocalRecognitionReview("review_1", {}),
      (error) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.error.code, "item_identity_required");
        assert.match(error.error.message, /商品身份/);
        return true;
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

function analysisResponse({
  horizon,
  currentAsk = "12.000000",
  currentBid = "11.000000"
}: {
  horizon: 7 | 30 | 90 | 180;
  currentAsk?: string | null;
  currentBid?: string | null;
}) {
  return {
    item_id: 1,
    external_key: "synthetic-alpha",
    item_name: "Synthetic Alpha",
    effective_inputs: {
      horizon,
      as_of: "2026-06-29T00:00:00Z",
      maximum_snapshot_age_seconds: 86400,
      minimum_snapshot_count: 3,
      fee_policy: {
        name: "gaijin_market",
        version: "1.0.0",
        nominal_fee_rate: "0.15",
        currency_quantum: "0.01",
        proceeds_rounding: "seller_proceeds_round_down"
      },
      market_rules: {
        name: "gaijin_market",
        version: "1.0.0",
        maximum_listing_price: "2000.00",
        maximum_sale_proceeds: "1700.00",
        currency_quantum: "0.01"
      }
    },
    status: "ok",
    strategy_name: "rule_based",
    strategy_version: "1.0.0",
    feature_version: "market_features_v1",
    observation_count: 3,
    first_observation_at: "2026-06-22T00:00:00Z",
    last_observation_at: "2026-06-29T00:00:00Z",
    current_ask: currentAsk,
    current_bid: currentBid,
    reference_sell_price: "11.000000",
    sale_proceeds: "9.350000",
    fee_amount: "1.650000",
    gross_profit: "-1.000000",
    net_profit: "-2.650000",
    net_roi: "-0.2208333333333333333333333333",
    break_even_sell_price: "14.11764705882352941176470588",
    break_even_reachable: true,
    maximum_net_profit: "1688.000000",
    spread_absolute: "1.000000",
    spread_ratio: "0.09090909090909090909090909091",
    median_bid: "10.000000",
    median_ask: "11.000000",
    price_volatility: "1.000000",
    liquidity_score: "100",
    risk_score: "50",
    confidence_score: "85",
    reason_codes: ["analysis_completed"]
  };
}

function localReviewResponse() {
  return {
    review_id: "review_1",
    created_at: "2026-07-01T00:00:00Z",
    expires_at: "2026-07-01T02:00:00Z",
    status: "pending_review",
    suggested_observed_at: "2026-07-01T00:00:00Z",
    image: null,
    recognition: {
      ocr_backend: "windows-ocr",
      ocr_backend_version: "fake",
      layout_name: "gaijin-market-desktop-v1",
      layout_version: "1.2.0",
      config_sha256: "a".repeat(64),
      parser_version: "parser",
      runner_version: "runner",
      processing_duration_ms: 1
    },
    ocr_candidate: {
      item_name_raw: "Synthetic Alpha",
      item_name_normalized: "Synthetic Alpha",
      best_bid: "12.34",
      best_ask: "13.00",
      total_bid_quantity: null,
      total_ask_quantity: 7
    },
    draft: {
      selected_item_id: null,
      item_key: null,
      final_item_name: "Synthetic Alpha",
      final_best_bid: "12.34",
      final_best_ask: "13.00",
      final_total_bid_quantity: null,
      final_total_ask_quantity: 7,
      observed_at: "2026-07-01T00:00:00Z",
      observed_at_source: "review_created_default",
      reviewer_note: null
    },
    ocr_evidence_summary: {
      fields: {},
      confidence_source: "unavailable",
      confidence_available: false
    },
    warnings: [],
    errors: [],
    candidate: null,
    confirmed_at: null,
    rejected_at: null
  };
}
