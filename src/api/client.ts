import type { ApiErrorPayload } from "../types/api";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export function apiUrl(path: string) {
  return `${API_BASE_URL}${path}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isLineageConflict() {
    return this.status === 409 || this.code === "lineage_conflict";
  }
}

export interface ApiResponse<T> {
  data: T;
  etag: string | null;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await requestJson<T>(path, { method: "GET", signal })).data;
}

export async function requestJson<T>(path: string, init: RequestInit): Promise<ApiResponse<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init.headers },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_failure", "无法连接 Web API，请确认服务已启动。", null);
  }

  const body = await parseJson(response);
  if (!response.ok) {
    const payload = body as Partial<ApiErrorPayload> | null;
    throw new ApiError(
      response.status,
      payload?.error?.code ?? `http_${response.status}`,
      payload?.error?.message ?? httpMessage(response.status),
      payload?.error?.details ?? null,
    );
  }
  return { data: body as T, etag: response.headers.get("ETag") };
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(response.status, "invalid_json", "服务返回了无法解析的数据。", null);
  }
}

function httpMessage(status: number) {
  if (status === 404) return "请求的项目或审核对象不存在。";
  if (status === 409) return "当前审核版本已经变化，请刷新后重试。";
  if (status === 422) return "提交的审核结论不符合当前审核规则。";
  if (status === 428) return "缺少审核版本保护，请刷新后重试。";
  if (status >= 500) return "服务暂时无法处理项目数据。";
  return "请求未成功。";
}
