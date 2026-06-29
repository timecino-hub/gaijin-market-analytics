import type {
  ApiError,
  CsvUploadResult,
  ImportJobResponse,
  ItemDetail,
  ItemListQuery,
  MarketSnapshot,
  PaginatedItemsResponse,
  SnapshotQuery
} from "./types";

export class ApiRequestError extends Error {
  readonly error: ApiError;

  constructor(error: ApiError) {
    super(error.message);
    this.name = "ApiRequestError";
    this.error = error;
  }
}

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export async function getItems(query: ItemListQuery): Promise<PaginatedItemsResponse> {
  return fetchJson<PaginatedItemsResponse>("/api/v1/items", query);
}

export async function getItem(itemId: string): Promise<ItemDetail> {
  return fetchJson<ItemDetail>(`/api/v1/items/${encodeURIComponent(itemId)}`);
}

export async function getItemSnapshots(
  itemId: string,
  query: SnapshotQuery
): Promise<MarketSnapshot[]> {
  return fetchJson<MarketSnapshot[]>(
    `/api/v1/items/${encodeURIComponent(itemId)}/snapshots`,
    query
  );
}

export async function uploadCsvImport(file: File): Promise<CsvUploadResult> {
  const formData = new FormData();
  formData.set("file", file);

  const response = await fetch(buildApiUrl("/api/v1/imports/csv"), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json"
    },
    body: formData
  });

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }

  return (await response.json()) as CsvUploadResult;
}

export async function getImportJob(jobId: number): Promise<ImportJobResponse> {
  return fetchJson<ImportJobResponse>(`/api/v1/imports/${encodeURIComponent(String(jobId))}`);
}

export function toDisplayError(error: unknown): ApiError {
  if (error instanceof ApiRequestError) {
    return error.error;
  }

  if (error instanceof TypeError) {
    return {
      status: 0,
      code: "api_unreachable",
      message: "API 服务不可用，请确认后端正在运行且 API 基础地址配置正确。"
    };
  }

  return {
    status: 0,
    code: "unknown_error",
    message: "请求失败，请稍后重试。"
  };
}

async function fetchJson<T>(
  path: string,
  query?: Record<string, string | undefined>
): Promise<T> {
  const response = await fetch(buildApiUrl(path, query), {
    cache: "no-store",
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }

  return (await response.json()) as T;
}

export function buildApiUrl(path: string, query?: Record<string, string | undefined>): string {
  const url = new URL(path, getApiBaseUrl());
  const params = new URLSearchParams();

  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") {
      params.set(key, value);
    }
  }

  url.search = params.toString();
  return url.toString();
}

function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
}

async function parseApiError(response: Response): Promise<ApiError> {
  const fallback = friendlyError(response.status);

  try {
    const body = (await response.json()) as {
      detail?: { code?: string; error_code?: string; message?: string } | string;
    };
    if (typeof body.detail === "object" && body.detail !== null) {
      const code = body.detail.code ?? body.detail.error_code ?? fallback.code;
      return {
        status: response.status,
        code,
        message:
          friendlyBusinessMessage(response.status, code) ??
          safeApiMessage(response.status, body.detail.message) ??
          fallback.message
      };
    }
  } catch {
    return fallback;
  }

  return fallback;
}

function friendlyError(status: number): ApiError {
  if (status === 413) {
    return {
      status,
      code: "file_too_large",
      message: "文件超过大小限制。CSV 文件最大为 10 MB。"
    };
  }

  if (status === 404) {
    return {
      status,
      code: "not_found",
      message: "未找到请求的资源。"
    };
  }

  if (status === 400 || status === 422) {
    return {
      status,
      code: "invalid_request",
      message: "请求参数无效，请检查后重试。"
    };
  }

  if (status === 415) {
    return {
      status,
      code: "invalid_file_type",
      message: "上传文件必须是 CSV。"
    };
  }

  if (status >= 500) {
    return {
      status,
      code: "api_error",
      message: "API 服务暂时无法完成请求，请稍后重试。"
    };
  }

  return {
    status,
    code: "api_error",
    message: "API 返回错误，请稍后重试。"
  };
}

function friendlyBusinessMessage(status: number, code: string): string | undefined {
  if (status === 413 || code === "file_too_large") {
    return "文件超过大小限制。CSV 文件最大为 10 MB。";
  }

  if (code === "invalid_extension" || code === "invalid_mime_type") {
    return "上传文件必须是 CSV。";
  }

  return undefined;
}

function safeApiMessage(status: number, message: string | undefined): string | undefined {
  if (!message || status >= 500 || looksSensitive(message)) {
    return undefined;
  }

  return message;
}

function looksSensitive(message: string): boolean {
  return /(?:Traceback|File "|\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|postgresql:\/\/|postgresql\+psycopg:\/\/|[A-Za-z]:\\|\/app\/|\/home\/)/i.test(
    message
  );
}
