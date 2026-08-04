import type { Ref } from "vue";

const tokenKey = "academic-search.access-token";
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000")
  .trim()
  .replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const getAccessToken = (): string | null => localStorage.getItem(tokenKey);

export const setAccessToken = (token: string): void => localStorage.setItem(tokenKey, token);

export const clearAccessToken = (): void => localStorage.removeItem(tokenKey);

export const apiUrl = (path: string): string => `${apiBaseUrl}${path}`;

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(apiUrl(path), { ...init, headers });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? payload.detail
        : null;
    const errorDetail = typeof detail === "object" && detail !== null ? detail : null;
    const isValidationError = Array.isArray(detail);
    const message =
      errorDetail && "message" in errorDetail && typeof errorDetail.message === "string"
        ? errorDetail.message
        : isValidationError
          ? "输入不符合格式要求，请检查后重试。"
          : `请求失败（HTTP ${response.status}）`;
    const code =
      errorDetail && "code" in errorDetail && typeof errorDetail.code === "string"
        ? errorDetail.code
        : null;
    throw new ApiError(message, response.status, code);
  }

  return payload as T;
}

export function isRefetchableError(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 500;
}

// 保留一个可被组合式函数引用的类型，方便后续将请求状态绑定到 Vue Ref。
export type AsyncRef<T> = Ref<T | undefined>;
