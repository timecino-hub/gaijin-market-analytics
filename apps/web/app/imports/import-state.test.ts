import assert from "node:assert/strict";
import test from "node:test";
import {
  importPageReducer,
  initialImportPageState,
  phaseForImportStatus,
  type ImportPageState
} from "./import-state.ts";
import type { ImportJobResponse } from "../../lib/types.ts";

const selectedFile = { name: "synthetic.csv", size: 12, type: "text/csv" };

function job(status: ImportJobResponse["status"]): ImportJobResponse {
  return {
    job_id: status === "duplicate" ? 2 : 1,
    status,
    filename: "synthetic.csv",
    checksum: "a".repeat(64),
    row_count: 1,
    valid_row_count: status === "duplicate" ? 0 : 1,
    invalid_row_count: 0,
    duplicate_of_job_id: status === "duplicate" ? 1 : null,
    error_report: {
      errors:
        status === "failed"
          ? [
              {
                row_number: 2,
                field: "best_ask",
                error_code: "must_be_positive",
                message: "best_ask must be greater than 0."
              }
            ]
          : [],
      warnings:
        status === "completed"
          ? [
              {
                row_number: 3,
                field: "observed_at",
                error_code: "duplicate_snapshot_existing",
                message: "Snapshot already exists."
              }
            ]
          : [],
      duplicate_of_job_id: status === "duplicate" ? 1 : null
    }
  };
}

test("phaseForImportStatus maps completed, duplicate, failed, and non-terminal states", () => {
  assert.equal(phaseForImportStatus("completed"), "success");
  assert.equal(phaseForImportStatus("duplicate"), "duplicate");
  assert.equal(phaseForImportStatus("failed"), "failed");
  assert.equal(phaseForImportStatus("pending"), "uploading");
  assert.equal(phaseForImportStatus("processing"), "uploading");
});

test("selecting a new valid file clears previous results and errors", () => {
  const previous: ImportPageState = {
    phase: "failed",
    file: selectedFile,
    result: job("failed"),
    error: { status: 400, code: "invalid_extension", message: "Old error" }
  };

  const next = importPageReducer(previous, {
    type: "select_file",
    file: { name: "new.csv", size: 16, type: "" },
    validation: { ok: true }
  });

  assert.equal(next.phase, "selected");
  assert.equal(next.result, null);
  assert.equal(next.error, null);
  assert.equal(next.file?.name, "new.csv");
});

test("client validation errors clear old import result", () => {
  const next = importPageReducer(
    { ...initialImportPageState, result: job("completed") },
    {
      type: "select_file",
      file: { name: "notes.txt", size: 16, type: "text/plain" },
      validation: {
        ok: false,
        code: "invalid_extension",
        message: "只能上传 .csv 文件。"
      }
    }
  );

  assert.equal(next.phase, "client_validation_error");
  assert.equal(next.result, null);
  assert.equal(next.error?.code, "invalid_extension");
});

test("start_upload prevents duplicate submission while already uploading", () => {
  const uploading: ImportPageState = {
    phase: "uploading",
    file: selectedFile,
    result: null,
    error: null
  };

  assert.equal(importPageReducer(uploading, { type: "start_upload" }), uploading);
});

test("upload_success stores completed, duplicate, and failed results", () => {
  for (const status of ["completed", "duplicate", "failed"] as const) {
    const next = importPageReducer(
      { ...initialImportPageState, file: selectedFile },
      { type: "upload_success", result: job(status) }
    );

    assert.equal(next.result?.status, status);
  }
});

test("network errors use network_error phase", () => {
  const next = importPageReducer(
    { ...initialImportPageState, file: selectedFile, phase: "uploading" },
    {
      type: "upload_error",
      error: {
        status: 0,
        code: "api_unreachable",
        message: "API 服务不可用"
      }
    }
  );

  assert.equal(next.phase, "network_error");
});
