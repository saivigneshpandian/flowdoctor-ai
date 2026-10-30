import { ApiError } from "@/lib/api-error";
import { mockApi } from "@/lib/mocks/client";
import type { ApiClient } from "@/lib/types";

export { ApiError };

export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";

// Same origin on purpose: next.config.ts proxies this to the backend, so the cookie is first-party.
const API_URL = "/api";

// FastAPI reports errors as {detail: string} or {detail: [{msg}]}.
function readErrorDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return null;
  }
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: { msg?: unknown }) => item?.msg)
      .filter((msg): msg is string => typeof msg === "string");
    return messages.length > 0 ? messages.join("; ") : null;
  }
  return null;
}

const READ_TIMEOUT_MS = 30_000;
// Chat runs LLM calls with a fallback, and repairs wait on n8n, so both can legitimately take a while.
const AGENT_TIMEOUT_MS = 120_000;

async function request<T>(
  path: string,
  body?: unknown,
  method = "POST",
  timeoutMs = AGENT_TIMEOUT_MS,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(
        `The backend did not answer within ${timeoutMs / 1000} seconds. It may still be working.`,
      );
    }
    throw new ApiError("Could not reach FlowDoctor. Check your connection and try again.");
  }

  const payload: unknown = await response.json().catch(() => null);

  // An expired session sends the user back to log in. /auth/* handles its own 401s.
  if (response.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") {
    window.location.assign("/login");
  }

  if (!response.ok) {
    throw new ApiError(
      readErrorDetail(payload) ?? `The backend returned an error (HTTP ${response.status}).`,
      response.status,
    );
  }
  if (payload === null) {
    throw new ApiError("The backend returned an unreadable response.", response.status);
  }
  return payload as T;
}

const realApi: ApiClient = {
  signup: (body) => request("/auth/signup", body, "POST", READ_TIMEOUT_MS),
  login: (body) => request("/auth/login", body, "POST", READ_TIMEOUT_MS),
  logout: () => request("/auth/logout", undefined, "POST", READ_TIMEOUT_MS),
  me: () => request("/auth/me", undefined, "GET", READ_TIMEOUT_MS),
  connect: (body) => request("/connect", body, "POST", READ_TIMEOUT_MS),
  getWorkflows: () => request("/workflows", undefined, "GET", READ_TIMEOUT_MS),
  getWorkflowError: (id) =>
    request(`/workflows/${encodeURIComponent(id)}/error`, undefined, "GET", READ_TIMEOUT_MS),
  listThreads: () => request("/threads", undefined, "GET", READ_TIMEOUT_MS),
  createThread: (body) => request("/threads", body, "POST", READ_TIMEOUT_MS),
  getThread: (id) => request(`/threads/${encodeURIComponent(id)}`, undefined, "GET", READ_TIMEOUT_MS),
  chat: (body) => request("/chat", body),
  approveFix: (id) => request(`/fix/${encodeURIComponent(id)}/approve`),
  rejectFix: (id) => request(`/fix/${encodeURIComponent(id)}/reject`),
  completeFix: (id) => request(`/fix/${encodeURIComponent(id)}/done`),
  getIncidents: () => request("/incidents", undefined, "GET", READ_TIMEOUT_MS),
};

export const api: ApiClient = USE_MOCKS ? mockApi : realApi;

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
