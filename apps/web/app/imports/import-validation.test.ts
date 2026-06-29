import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_CSV_UPLOAD_BYTES,
  abbreviateChecksum,
  formatFileSize,
  validateCsvFile
} from "./import-validation.ts";

test("validateCsvFile rejects missing files", () => {
  assert.deepEqual(validateCsvFile(null), {
    ok: false,
    code: "missing_file",
    message: "请选择一个 CSV 文件。"
  });
});

test("validateCsvFile rejects empty CSV files", () => {
  const result = validateCsvFile({ name: "empty.csv", size: 0 });

  assert.equal(result.ok, false);
  assert.equal(result.code, "empty_file");
});

test("validateCsvFile rejects non CSV extensions without relying on MIME", () => {
  const result = validateCsvFile({ name: "synthetic.txt", size: 12 });

  assert.equal(result.ok, false);
  assert.equal(result.code, "invalid_extension");
});

test("validateCsvFile rejects files over 10 MB", () => {
  const result = validateCsvFile({ name: "large.csv", size: MAX_CSV_UPLOAD_BYTES + 1 });

  assert.equal(result.ok, false);
  assert.equal(result.code, "file_too_large");
});

test("validateCsvFile accepts non-empty CSV files", () => {
  assert.deepEqual(validateCsvFile({ name: "synthetic.CSV", size: 12 }), { ok: true });
});

test("formatFileSize renders browser file sizes without reading content", () => {
  assert.equal(formatFileSize(12), "12 B");
  assert.equal(formatFileSize(2048), "2.0 KB");
  assert.equal(formatFileSize(2 * 1024 * 1024), "2.0 MB");
});

test("abbreviateChecksum keeps long checksums readable", () => {
  assert.equal(abbreviateChecksum("a".repeat(64)), "aaaaaaaaaa...aaaaaaaa");
});
