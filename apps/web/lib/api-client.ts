import type {
  ApiError,
  CsvUploadResult,
  ImportJobResponse,
  ItemAnalysisQuery,
  ItemAnalysisResponse,
  ItemDetail,
  ItemListQuery,
  LocalExtensionPairingCode,
  LocalExtensionStatus,
  LocalRecognitionCapabilities,
  LocalRecognitionDraftPayload,
  LocalRecognitionReview,
  LocalRecognitionReviewCreate,
  LocalRecognitionReviewList,
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

export async function getItemAnalysis(
  itemId: string,
  query: ItemAnalysisQuery,
  signal?: AbortSignal
): Promise<ItemAnalysisResponse> {
  return fetchJson<ItemAnalysisResponse>(
    `/api/v1/items/${encodeURIComponent(itemId)}/analysis`,
    query,
    signal
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

export async function getLocalRecognitionCapabilities(): Promise<LocalRecognitionCapabilities> {
  return fetchJson<LocalRecognitionCapabilities>("/api/v1/local-recognition/capabilities");
}

export async function createLocalExtensionPairingCode(): Promise<LocalExtensionPairingCode> {
  const response = await fetch(buildApiUrl("/api/v1/local-recognition/pairing-codes"), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }

  return (await response.json()) as LocalExtensionPairingCode;
}

export async function getLocalExtensionStatus(signal?: AbortSignal): Promise<LocalExtensionStatus> {
  return fetchJson<LocalExtensionStatus>("/api/v1/local-recognition/extension-status", undefined, signal);
}

export async function revokeLocalExtensionPairing(pairingId: string): Promise<void> {
  const response = await fetch(
    buildApiUrl(`/api/v1/local-recognition/pairings/${encodeURIComponent(pairingId)}`),
    {
      method: "DELETE",
      cache: "no-store",
      headers: {
        Accept: "application/json"
      }
    }
  );

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }
}

export async function uploadLocalRecognitionReview(
  file: File
): Promise<LocalRecognitionReviewCreate> {
  const formData = new FormData();
  formData.set("file", file);

  const response = await fetch(buildApiUrl("/api/v1/local-recognition/reviews"), {
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

  return (await response.json()) as LocalRecognitionReviewCreate;
}

export async function getLocalRecognitionReviews(
  signal?: AbortSignal
): Promise<LocalRecognitionReviewList> {
  return fetchJson<LocalRecognitionReviewList>(
    "/api/v1/local-recognition/reviews",
    undefined,
    signal
  );
}

export async function getLocalRecognitionReview(
  reviewId: string,
  signal?: AbortSignal
): Promise<LocalRecognitionReview> {
  return fetchJson<LocalRecognitionReview>(
    `/api/v1/local-recognition/reviews/${encodeURIComponent(reviewId)}`,
    undefined,
    signal
  );
}

export async function patchLocalRecognitionReview(
  reviewId: string,
  payload: LocalRecognitionDraftPayload
): Promise<LocalRecognitionReview> {
  return sendJson<LocalRecognitionReview>(
    `/api/v1/local-recognition/reviews/${encodeURIComponent(reviewId)}`,
    "PATCH",
    payload
  );
}

export async function confirmLocalRecognitionReview(
  reviewId: string,
  payload: LocalRecognitionDraftPayload
): Promise<LocalRecognitionReview> {
  return sendJson<LocalRecognitionReview>(
    `/api/v1/local-recognition/reviews/${encodeURIComponent(reviewId)}/confirm`,
    "POST",
    payload
  );
}

export async function rejectLocalRecognitionReview(
  reviewId: string,
  reviewerNote: string | null
): Promise<LocalRecognitionReview> {
  return sendJson<LocalRecognitionReview>(
    `/api/v1/local-recognition/reviews/${encodeURIComponent(reviewId)}/reject`,
    "POST",
    { reviewer_note: reviewerNote }
  );
}

export async function markLocalRecognitionReviewUnreadable(
  reviewId: string,
  reviewerNote: string | null
): Promise<LocalRecognitionReview> {
  return sendJson<LocalRecognitionReview>(
    `/api/v1/local-recognition/reviews/${encodeURIComponent(reviewId)}/unreadable`,
    "POST",
    { reviewer_note: reviewerNote }
  );
}

export async function clearLocalRecognitionReviews(): Promise<{ cleared: number }> {
  const response = await fetch(buildApiUrl("/api/v1/local-recognition/reviews", { confirm: "true" }), {
    method: "DELETE",
    cache: "no-store",
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }

  return (await response.json()) as { cleared: number };
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
  query?: Record<string, string | undefined>,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(buildApiUrl(path, query), {
    cache: "no-store",
    signal,
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    throw new ApiRequestError(await parseApiError(response));
  }

  return (await response.json()) as T;
}

async function sendJson<T>(
  path: string,
  method: "POST" | "PATCH",
  payload: unknown
): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    method,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
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
      message: "上传文件格式不受支持。"
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
  if (code === "item_not_found") {
    return "请求的商品不存在。";
  }

  if (code === "invalid_horizon") {
    return "分析周期必须是 7、30、90 或 180 天。";
  }

  if (code === "fee_rate_not_configurable") {
    return "Gaijin Market 使用固定 15% 名义费率，并将卖家结算所得向下取整到 0.01 GJN。";
  }

  if (code === "invalid_as_of") {
    return "分析基准时间必须是带时区的 ISO-8601 时间。";
  }

  if (code === "analysis_input_error") {
    return "分析输入未通过后端合同校验，请检查商品数据、手续费率和基准时间。";
  }

  if (code === "strategy_not_available") {
    return "当前分析策略不可用，请稍后重试或检查后端配置。";
  }

  if (code === "invalid_analytics_configuration") {
    return "分析配置无效，请检查后端运行配置。";
  }

  if (status === 413 || code === "file_too_large") {
    return "文件超过大小限制。CSV 文件最大为 10 MB。";
  }

  if (code === "invalid_extension" || code === "invalid_mime_type") {
    return "上传文件必须是 CSV。";
  }

  const localRecognitionMessages: Record<string, string> = {
    review_not_found: "复核记录不存在，可能已过期或后端已重启。",
    review_expired: "复核记录已过期。",
    review_not_editable: "当前复核记录状态不可编辑。",
    review_store_full: "本地内存复核队列已满，请清理后重试。",
    unsupported_image_format: "只支持 PNG 或 JPEG 截图。",
    image_signature_mismatch: "图片扩展名与文件内容不一致。",
    image_too_large: "截图超过 10 MB 限制。",
    image_dimensions_too_large: "截图解码后尺寸超过 40MP 限制。",
    recognition_failed: "本地 OCR 处理失败，请检查 Windows OCR 可用性。",
    item_identity_required: "确认前必须明确商品身份。",
    item_identity_conflict: "请选择已有商品或填写管理员身份，不能同时冲突。",
    invalid_price_string: "价格必须以字符串传输，并满足市场价格规则。",
    price_out_of_market_range: "价格必须满足 0 < price <= 2000.00。",
    invalid_quantity: "数量必须是非负整数或留空。",
    observed_at_timezone_required: "观测时间必须包含时区。",
    observed_at_in_future: "观测时间不能明显晚于当前时间。",
    invalid_status_transition: "当前状态不允许该操作。",
    clear_confirmation_required: "清空内存复核记录需要显式确认。",
    local_management_origin_required: "本地 Bridge 管理请求缺少允许的 Origin。",
    local_management_origin_denied: "本地 Bridge 管理请求来源不被允许。",
    pairing_code_invalid: "配对码无效，请重新生成后再试。",
    pairing_code_expired: "配对码已过期，请重新生成。",
    pairing_code_consumed: "配对码已被使用，请重新生成。",
    pairing_code_attempts_exceeded: "配对码尝试次数过多，已失效。",
    pairing_store_full: "本地扩展配对存储已满，请撤销旧配对后重试。",
    pairing_not_found: "配对不存在，可能已撤销或后端已重启。",
    extension_token_required: "扩展未完成配对或缺少本地令牌。",
    extension_token_invalid: "扩展令牌无效，需要重新配对。",
    extension_token_revoked: "扩展配对已撤销，需要重新配对。",
    extension_rate_limited: "扩展上传过于频繁，请稍后再试。",
    extension_loopback_required: "扩展上传只能来自本机 loopback。",
    source_url_invalid: "扩展提供的页面 URL 无法安全保存。",
    source_url_too_long: "扩展提供的页面 URL 过长。",
    source_title_too_long: "扩展提供的页面标题过长。"
  };

  if (code in localRecognitionMessages) {
    return localRecognitionMessages[code];
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
