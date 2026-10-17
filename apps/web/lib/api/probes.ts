"use client";

/**
 * Dependency probes.
 *
 * The service exposes health only for itself, FalkorDB and the LLM provider.
 * Artifact availability is observable through the endpoints that read the
 * artifacts, so the workstation probes those directly and reports what each one
 * actually answered. No probe is inferred from another.
 */

import type { UseQueryResult } from "@tanstack/react-query";

import { ApiError } from "@/lib/api/client";
import { useAlerts, useBenchmarks, useCases, useHealth } from "@/lib/api/hooks";
import type {
  AlertsResponse,
  BenchmarksResponse,
  CaseListResponse,
  HealthResponse,
} from "@/lib/api/types";

export type ProbeState = "ok" | "unavailable" | "failed" | "loading";

export interface Probe {
  id: string;
  label: string;
  /** What the probe reads, in contract terms. */
  reads: string;
  state: ProbeState;
  /** The API's own detail string, or a plain description of the outcome. */
  detail: string;
  /** What an operator can do about a failure, when the cause is identifiable. */
  remedy: string | null;
}

function stateOf(query: UseQueryResult<unknown, Error>): ProbeState {
  if (query.isPending) return "loading";
  if (query.isSuccess) return "ok";
  if (query.error instanceof ApiError && query.error.isUnavailable) return "unavailable";
  return "failed";
}

function detailOf(
  query: UseQueryResult<unknown, Error>,
  successDetail: string,
): string {
  if (query.isPending) return "Probe in flight.";
  if (query.isSuccess) return successDetail;
  const error = query.error;
  if (error instanceof ApiError) {
    return error.status === 0 ? "The API service did not respond." : error.detail;
  }
  return error instanceof Error ? error.message : "Unknown failure.";
}

export interface DependencyProbes {
  health: UseQueryResult<HealthResponse, Error>;
  alerts: UseQueryResult<AlertsResponse, Error>;
  benchmarks: UseQueryResult<BenchmarksResponse, Error>;
  cases: UseQueryResult<CaseListResponse, Error>;
  probes: Probe[];
  refetchAll: () => void;
  isFetching: boolean;
}

export function useDependencyProbes(): DependencyProbes {
  const health = useHealth(30_000);
  const alerts = useAlerts(1, 0);
  const benchmarks = useBenchmarks();
  const cases = useCases(null, 1, 0);

  const probes: Probe[] = [
    {
      id: "api",
      label: "API service",
      reads: "GET /api/health",
      state: stateOf(health),
      detail: detailOf(
        health,
        health.data ? `${health.data.status} · version ${health.data.version}` : "healthy",
      ),
      remedy: null,
    },
    {
      id: "falkordb",
      label: "FalkorDB graph",
      reads: "GET /api/health → falkordb",
      state:
        health.isPending
          ? "loading"
          : health.isError
            ? "failed"
            : health.data.falkordb === "available"
              ? "ok"
              : health.data.falkordb === "unavailable"
                ? "unavailable"
                : "unavailable",
      detail: health.isPending
        ? "Probe in flight."
        : health.isError
          ? "Health did not answer, so the graph status is unknown."
          : health.data.falkordb === "available"
            ? "A query connection succeeded."
            : health.data.falkordb === "not_configured"
              ? "No host is configured, so graph endpoints refuse with 503."
              : "The host is configured but did not accept a connection.",
      remedy:
        health.data !== undefined && health.data.falkordb !== "available"
          ? "Start FalkorDB (docker compose up -d falkordb) and load the graph artifact."
          : null,
    },
    {
      id: "scores",
      label: "Inference score artifact",
      reads: "GET /api/alerts",
      state: stateOf(alerts),
      detail: detailOf(
        alerts,
        alerts.data ? `${alerts.data.total} scored transactions readable` : "readable",
      ),
      remedy:
        stateOf(alerts) === "unavailable"
          ? "Publish the inference scores and point TRACEAML_SCORES_PATH at them."
          : null,
    },
    {
      id: "benchmarks",
      label: "Benchmark artifacts",
      reads: "GET /api/benchmarks",
      state: stateOf(benchmarks),
      detail: detailOf(
        benchmarks,
        benchmarks.data
          ? `${Object.keys(benchmarks.data.models).length} model metric sets${
              benchmarks.data.performance ? " and one performance report" : ""
            } readable`
          : "readable",
      ),
      remedy:
        stateOf(benchmarks) === "unavailable"
          ? "Publish the baseline metrics, the graph-ablation metrics and the performance report, or adjust the TRACEAML_*_PATH variables."
          : null,
    },
    {
      id: "cases",
      label: "Case store",
      reads: "GET /api/cases",
      state: stateOf(cases),
      detail: detailOf(
        cases,
        cases.data ? `${cases.data.total} cases readable` : "readable",
      ),
      remedy:
        stateOf(cases) === "unavailable"
          ? "Check that TRACEAML_CASE_DB_PATH is writable by the API process."
          : null,
    },
  ];

  return {
    health,
    alerts,
    benchmarks,
    cases,
    probes,
    refetchAll: () => {
      void health.refetch();
      void alerts.refetch();
      void benchmarks.refetch();
      void cases.refetch();
    },
    isFetching:
      health.isFetching || alerts.isFetching || benchmarks.isFetching || cases.isFetching,
  };
}
