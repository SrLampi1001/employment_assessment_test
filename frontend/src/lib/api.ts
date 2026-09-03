import type { ProblemDetail } from "./types";

export const API_BASE_URL =
  (import.meta.env["VITE_API_BASE_URL"] as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: string;
  title: string;

  constructor(status: number, problem?: Partial<ProblemDetail>) {
    super(problem?.detail || problem?.title || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.title = problem?.title ?? "Error";
    this.detail = problem?.detail ?? problem?.title ?? `Request failed (${status})`;
  }
}

export class NetworkError extends Error {
  constructor() {
    super("network");
    this.name = "NetworkError";
  }
}

export interface ApiResult<T> {
  data: T;
  headers: Headers;
  status: number;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null,
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  if (token) headers["Authorization"] = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch {
    throw new NetworkError();
  }

  if (response.status === 204) {
    return { data: undefined as T, headers: response.headers, status: response.status };
  }

  if (!response.ok) {
    let problem: Partial<ProblemDetail> | undefined;
    try {
      problem = (await response.json()) as Partial<ProblemDetail>;
    } catch {
      problem = undefined;
    }
    throw new ApiError(response.status, problem);
  }

  let data: T;
  try {
    data = (await response.json()) as T;
  } catch {
    data = undefined as T;
  }

  return { data, headers: response.headers, status: response.status };
}

export function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : "";
}

export function decodeJwtSub(token: string): string | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))) as {
      sub?: string;
    };
    return json.sub ?? null;
  } catch {
    return null;
  }
}

export function newClientRef(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ref-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
