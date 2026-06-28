import type {
  ApiError,
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

export function toDisplayError(error: unknown): ApiError {
  if (error instanceof ApiRequestError) {
    return error.error;
  }

  if (error instanceof TypeError) {
    return {
      status: 0,
      code: "api_unreachable",
      message: "无法连接 API 服务，请确认后端正在运行并且 API 基础地址配置正确。"
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
      return {
        status: response.status,
        code: body.detail.code ?? body.detail.error_code ?? fallback.code,
        message: body.detail.message ?? fallback.message
      };
    }
  } catch {
    return fallback;
  }

  return fallback;
}

function friendlyError(status: number): ApiError {
  if (status === 404) {
    return {
      status,
      code: "item_not_found",
      message: "未找到请求的商品。"
    };
  }

  if (status === 400 || status === 422) {
    return {
      status,
      code: "invalid_query",
      message: "查询参数无效，请检查筛选、分页或时间范围。"
    };
  }

  return {
    status,
    code: "api_error",
    message: "API 返回错误，请稍后重试。"
  };
}
