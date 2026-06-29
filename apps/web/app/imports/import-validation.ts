export const MAX_CSV_UPLOAD_BYTES = 10 * 1024 * 1024;

export type ClientValidationResult =
  | { ok: true }
  | { ok: false; code: "missing_file" | "empty_file" | "invalid_extension" | "file_too_large"; message: string };

export function validateCsvFile(
  file: Pick<File, "name" | "size"> | null | undefined
): ClientValidationResult {
  if (!file) {
    return {
      ok: false,
      code: "missing_file",
      message: "请选择一个 CSV 文件。"
    };
  }

  if (!hasCsvExtension(file.name)) {
    return {
      ok: false,
      code: "invalid_extension",
      message: "只能上传 .csv 文件。"
    };
  }

  if (file.size === 0) {
    return {
      ok: false,
      code: "empty_file",
      message: "CSV 文件为空，无法上传。"
    };
  }

  if (file.size > MAX_CSV_UPLOAD_BYTES) {
    return {
      ok: false,
      code: "file_too_large",
      message: "文件超过大小限制。CSV 文件最大为 10 MB。"
    };
  }

  return { ok: true };
}

export function hasCsvExtension(filename: string): boolean {
  return filename.trim().toLowerCase().endsWith(".csv");
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function abbreviateChecksum(checksum: string): string {
  if (checksum.length <= 18) {
    return checksum;
  }

  return `${checksum.slice(0, 10)}...${checksum.slice(-8)}`;
}
