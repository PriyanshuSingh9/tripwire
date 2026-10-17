"use client";

import { useCallback, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { useInvestigation } from "@/lib/api/hooks";
import type { InvestigationRequest, InvestigationResponse } from "@/lib/api/types";
import { appendInvestigation, newAttemptId } from "@/lib/runs/log";
import { saveLastRun } from "@/lib/runs/last-result";

export interface RunAttempt {
  request: InvestigationRequest;
  startedAt: number;
}

export interface LoggedInvestigation {
  run: (
    request: InvestigationRequest,
    onSuccess?: (response: InvestigationResponse) => void,
  ) => void;
  result: InvestigationResponse | null;
  attempt: RunAttempt | null;
  error: Error | null;
  isPending: boolean;
  reset: () => void;
}

/**
 * Investigation mutation that also records what actually happened in this
 * browser. Successes and failures both land in the local log, so Overview and
 * System can report real activity and real failures without a backend history
 * endpoint.
 */
export function useLoggedInvestigation(): LoggedInvestigation {
  const mutation = useInvestigation();
  const [result, setResult] = useState<InvestigationResponse | null>(null);
  const [attempt, setAttempt] = useState<RunAttempt | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const run = useCallback(
    (request: InvestigationRequest, onSuccess?: (response: InvestigationResponse) => void) => {
      setError(null);
      setResult(null);
      const startedAt = performance.now();
      setAttempt({ request, startedAt });

      mutation.mutate(request, {
        onSuccess: (response) => {
          const duration = performance.now() - startedAt;
          appendInvestigation({
            attempt_id: newAttemptId(),
            attempted_at: new Date().toISOString(),
            transaction_id: request.transaction_id,
            status: "ok",
            investigation_id: response.investigation_id,
            outcome: response.outcome,
            decision: response.decision,
            risk_score: response.risk_score,
            client_duration_ms: Math.round(duration),
            http_status: 200,
            error_detail: null,
            evidence_counts: {
              transactions: response.evidence.transactions.length,
              accounts: response.evidence.accounts.length,
              paths: response.evidence.paths.length,
            },
            case_id: response.action?.case_id ?? null,
            action_status: response.action?.status ?? null,
            tool_run_count: response.tool_trace.length,
          });
          saveLastRun({ request, response, at: new Date().toISOString() });
          setResult(response);
          onSuccess?.(response);
        },
        onError: (mutationError) => {
          const duration = performance.now() - startedAt;
          const apiError = mutationError instanceof ApiError ? mutationError : null;
          appendInvestigation({
            attempt_id: newAttemptId(),
            attempted_at: new Date().toISOString(),
            transaction_id: request.transaction_id,
            status: "error",
            investigation_id: null,
            outcome: null,
            decision: null,
            risk_score: null,
            client_duration_ms: Math.round(duration),
            http_status: apiError ? apiError.status : null,
            error_detail: mutationError.message,
            evidence_counts: null,
            case_id: null,
            action_status: null,
            tool_run_count: null,
          });
          setError(mutationError);
        },
      });
    },
    [mutation],
  );

  return {
    run,
    result,
    attempt,
    error,
    isPending: mutation.isPending,
    reset: () => {
      setResult(null);
      setError(null);
      setAttempt(null);
      mutation.reset();
    },
  };
}
