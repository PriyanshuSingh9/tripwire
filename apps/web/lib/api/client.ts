/**
 * Thin typed client for the TraceAML API.
 *
 * Requests go to the same-origin `/api` path, which Next.js rewrites to
 * `TRACEAML_API_ORIGIN` (see `next.config.ts`). Nothing is mocked here: a
 * failed request throws an `ApiError` carrying the status and the API's fixed
 * `detail` string, and callers render an explicit failure state.
 */

import type {
  AlertsResponse,
  BenchmarksResponse,
  CaseCreateRequest,
  CaseItem,
  CaseListResponse,
  CaseReviewRequest,
  CaseStatus,
  HealthResponse,
  InvestigationRequest,
  InvestigationResponse,
} from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }

  /** True when the failure is a transport failure rather than an HTTP status. */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  /** 503 means a required dependency or artifact is not available. */
  get isUnavailable(): boolean {
    return this.status === 503;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isTimeout(): boolean {
    return this.status === 504;
  }
}

function readErrorMessage(status: number, body: unknown): string {
  if (body !== null && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
    // FastAPI validation errors use a list of objects.
    if (Array.isArray(detail)) {
      const parts = detail
        .map((entry) =>
          entry !== null && typeof entry === "object" && "msg" in entry
            ? String((entry as { msg: unknown }).msg)
            : null,
        )
        .filter((part): part is string => part !== null);
      if (parts.length > 0) {
        return parts.join("; ");
      }
    }
  }
  return `request failed with status ${status}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch {
    // Transport failure: the API service did not answer at all.
    throw new ApiError(0, "the API service did not respond");
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiError(response.status, readErrorMessage(response.status, body));
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, "the API returned a malformed response");
  }
}

export const api = {
  health(signal?: AbortSignal): Promise<HealthResponse> {
    return requestJson<HealthResponse>("/health", { signal });
  },

  alerts(limit: number, offset: number, signal?: AbortSignal): Promise<AlertsResponse> {
    const query = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    return requestJson<AlertsResponse>(`/alerts?${query.toString()}`, { signal });
  },

  benchmarks(signal?: AbortSignal): Promise<BenchmarksResponse> {
    return requestJson<BenchmarksResponse>("/benchmarks", { signal });
  },

  investigate(
    body: InvestigationRequest,
    signal?: AbortSignal,
  ): Promise<InvestigationResponse> {
    return requestJson<InvestigationResponse>("/investigations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  },

  listCases(
    params: { status?: CaseStatus; limit?: number; offset?: number },
    signal?: AbortSignal,
  ): Promise<CaseListResponse> {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    query.set("limit", String(params.limit ?? 50));
    query.set("offset", String(params.offset ?? 0));
    return requestJson<CaseListResponse>(`/cases?${query.toString()}`, { signal });
  },

  getCase(caseId: string, signal?: AbortSignal): Promise<CaseItem> {
    return requestJson<CaseItem>(`/cases/${encodeURIComponent(caseId)}`, { signal });
  },

  createCase(body: CaseCreateRequest): Promise<CaseItem> {
    return requestJson<CaseItem>("/cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  reviewCase(caseId: string, body: CaseReviewRequest): Promise<CaseItem> {
    return requestJson<CaseItem>(`/cases/${encodeURIComponent(caseId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
};

export const ALERT_PAGE_MAX = 200;
export const CASE_PAGE_MAX = 100;
