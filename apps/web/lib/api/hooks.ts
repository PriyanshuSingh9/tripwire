"use client";

/**
 * TanStack Query hooks over the typed API client.
 *
 * Cache and loading conventions live here so pages do not duplicate request
 * logic. Mutations invalidate exactly the keys they change. Nothing falls back
 * to placeholder data: an unavailable dependency stays visible as unavailable.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { api } from "./client";
import { queryKeys } from "./keys";
import type {
  AlertItem,
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

export function useHealth(refetchIntervalMs?: number): UseQueryResult<HealthResponse, Error> {
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: refetchIntervalMs,
    staleTime: 5_000,
  });
}

export function useAlerts(limit: number, offset = 0): UseQueryResult<AlertsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.alerts(limit, offset),
    queryFn: ({ signal }) => api.alerts(limit, offset, signal),
    staleTime: 30_000,
  });
}

export interface ReviewQueueCount {
  /** Review decisions found in the ranked queue. */
  count: number;
  /**
   * True when the decision boundary was reached, which makes `count` exact:
   * ranked order is score-descending and the review cut is a score threshold,
   * so every review row sits above every allow row.
   */
  exact: boolean;
  /**
   * The ranked rows loaded while counting, best first. The overview figures
   * draw from exactly this window, so drawing them costs no extra request.
   */
  items: AlertItem[];
}

/**
 * Counts review decisions by paging the ranked queue until the first allow
 * row. Bounded: if the review band is wider than the page budget, the result
 * is reported as "at least" (`exact: false`) instead of guessing.
 */
export function useReviewAlertCount(): UseQueryResult<ReviewQueueCount, Error> {
  return useQuery({
    queryKey: [...queryKeys.alerts(200, 0), "review-count"] as const,
    queryFn: async ({ signal }) => {
      const pageSize = 200;
      const maxPages = 10;
      const items: AlertItem[] = [];
      for (let page = 0; page < maxPages; page += 1) {
        const offset = page * pageSize;
        const data = await api.alerts(pageSize, offset, signal);
        items.push(...data.items);
        const allowIndex = data.items.findIndex((item) => item.decision === "allow");
        if (allowIndex >= 0) {
          return { count: offset + allowIndex, exact: true, items };
        }
        if (data.items.length < pageSize || offset + data.items.length >= data.total) {
          return { count: offset + data.items.length, exact: true, items };
        }
      }
      return { count: maxPages * pageSize, exact: false, items };
    },
    staleTime: 60_000,
  });
}

export function useBenchmarks(): UseQueryResult<BenchmarksResponse, Error> {
  return useQuery({
    queryKey: queryKeys.benchmarks(),
    queryFn: ({ signal }) => api.benchmarks(signal),
    staleTime: 60_000,
  });
}

export function useCases(
  status: CaseStatus | null,
  limit: number,
  offset = 0,
  enabled = true,
): UseQueryResult<CaseListResponse, Error> {
  return useQuery({
    queryKey: queryKeys.cases(status, limit, offset),
    queryFn: ({ signal }) => api.listCases({ status: status ?? undefined, limit, offset }, signal),
    enabled,
    staleTime: 10_000,
  });
}

export function useCase(caseId: string | null): UseQueryResult<CaseItem, Error> {
  return useQuery({
    queryKey: queryKeys.case(caseId ?? ""),
    queryFn: ({ signal }) => api.getCase(caseId as string, signal),
    enabled: caseId !== null && caseId.length > 0,
    staleTime: 5_000,
  });
}

export function useCreateCase(): UseMutationResult<CaseItem, Error, CaseCreateRequest> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CaseCreateRequest) => api.createCase(body),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: ["cases"] });
      client.setQueryData(queryKeys.case(created.case_id), created);
    },
  });
}

export function useReviewCase(): UseMutationResult<
  CaseItem,
  Error,
  { caseId: string; body: CaseReviewRequest }
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, body }: { caseId: string; body: CaseReviewRequest }) =>
      api.reviewCase(caseId, body),
    onSuccess: (updated) => {
      void client.invalidateQueries({ queryKey: ["cases"] });
      client.setQueryData(queryKeys.case(updated.case_id), updated);
    },
  });
}

export function useInvestigation(): UseMutationResult<
  InvestigationResponse,
  Error,
  InvestigationRequest
> {
  return useMutation({
    mutationFn: (body: InvestigationRequest) => api.investigate(body),
  });
}
