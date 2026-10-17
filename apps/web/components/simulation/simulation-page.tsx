"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, CircleDashed, Play, Search } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, TableSkeleton } from "@/components/ui/states";
import { AgentWindow } from "@/components/agent/agent-window";
import type { GraphMode } from "@/components/graph/graph-toolbar";
import type { GraphSelection } from "@/components/graph/graph-canvas";
import {
  DEFAULT_RUN_FORM,
  RunForm,
  validateRunForm,
  type RunFormState,
} from "@/components/simulation/run-form";
import { RunLogCard } from "@/components/runs/run-log-card";
import { useHealth } from "@/lib/api/hooks";
import type { InvestigationRequest } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";
import { buildGraphModel } from "@/lib/graph/model";
import type { RunAttempt } from "@/lib/runs/use-investigation-run";
import { useInvestigationLog } from "@/lib/runs/log";
import { useLastRun } from "@/lib/runs/last-result";
import { useLoggedInvestigation } from "@/lib/runs/use-investigation-run";

/**
 * Simulation workspace.
 *
 * The page runs one bounded investigation at a time against the real service,
 * keeps the previous result visible while a new run is in flight, and shows
 * the declared bounds next to the evidence they produced.
 */
export function SimulationPage(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlTransactionId = searchParams.get("transaction_id") ?? "";

  const [form, setForm] = useState<RunFormState>({
    ...DEFAULT_RUN_FORM,
    transactionId: urlTransactionId,
  });
  const [validationError, setValidationError] = useState<string | null>(null);
  const [selection, setSelection] = useState<GraphSelection | null>(null);
  const [mode, setMode] = useState<GraphMode>("all");
  const [pathIndex, setPathIndex] = useState(0);

  const { run, result, attempt, error, isPending } = useLoggedInvestigation();
  const health = useHealth();
  const log = useInvestigationLog();
  const lastRun = useLastRun();

  // A run that happened earlier in this tab survives navigation: the previous
  // graph and evidence come back instead of an empty page.
  const activeResult = result ?? lastRun?.response ?? null;
  const activeAttempt = useMemo<RunAttempt | null>(
    () =>
      attempt ??
      (lastRun !== null ? { request: lastRun.request, startedAt: Date.parse(lastRun.at) } : null),
    [attempt, lastRun],
  );
  const showingCached = result === null && lastRun !== null;

  const llmConfigured = health.data?.llm === "configured";

  const model = useMemo(
    () => (activeResult ? buildGraphModel(activeResult) : null),
    [activeResult],
  );

  // Prefill the anchor from the cached run when the URL names none.
  const prefilledRef = useRef(false);
  useEffect(() => {
    if (prefilledRef.current || lastRun === null) return;
    prefilledRef.current = true;
    setForm((current) =>
      current.transactionId.length === 0
        ? { ...current, transactionId: lastRun.request.transaction_id }
        : current,
    );
  }, [lastRun]);

  // Keep the address bar in step with the anchor under investigation so the
  // run stays shareable and reload-safe.
  const lastSyncedRef = useRef(urlTransactionId);
  useEffect(() => {
    if (urlTransactionId === lastSyncedRef.current) return;
    lastSyncedRef.current = urlTransactionId;
    setForm((current) => ({ ...current, transactionId: urlTransactionId }));
  }, [urlTransactionId]);

  const syncUrl = useCallback(
    (transactionId: string) => {
      lastSyncedRef.current = transactionId;
      const next = `/simulation?transaction_id=${encodeURIComponent(transactionId)}`;
      router.replace(next, { scroll: false });
    },
    [router],
  );

  const submit = useCallback(
    (state: RunFormState, overrides?: Partial<InvestigationRequest>) => {
      const problem = validateRunForm(state);
      if (problem) {
        setValidationError(problem);
        return;
      }
      setValidationError(null);
      const request: InvestigationRequest = {
        transaction_id: state.transactionId.trim(),
        max_hops: Number(state.maxHops),
        time_window_seconds: Number(state.timeWindowSeconds),
        result_limit: Number(state.resultLimit),
        use_llm: state.useLlm,
        act: state.act,
        ...overrides,
      };
      run(request, () => {
        setSelection(null);
        setMode("all");
        setPathIndex(0);
        syncUrl(request.transaction_id);
      });
    },
    [run, syncUrl],
  );

  const widenBounds = useCallback(() => {
    if (!activeAttempt) return;
    const nextLimit = Math.min(100, activeAttempt.request.result_limit + 25);
    const nextHops = activeAttempt.request.max_hops < 3 ? activeAttempt.request.max_hops + 1 : 3;
    const nextState: RunFormState = {
      transactionId: activeAttempt.request.transaction_id,
      maxHops: String(nextHops),
      timeWindowSeconds: String(activeAttempt.request.time_window_seconds),
      resultLimit: String(nextLimit),
      useLlm: activeAttempt.request.use_llm,
      act: activeAttempt.request.act,
    };
    setForm(nextState);
    submit(nextState, { max_hops: nextHops, result_limit: nextLimit });
  }, [activeAttempt, submit]);

  const runLog = (
    <RunLogCard
      entries={log}
      onRerun={(transactionId) => {
        const nextState = { ...form, transactionId };
        setForm(nextState);
        submit(nextState);
      }}
    />
  );

  return (
    <div className="space-y-4">
      <RunForm
        state={form}
        onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
        onSubmit={() => submit(form)}
        onReset={() => {
          setForm(DEFAULT_RUN_FORM);
          setValidationError(null);
          setSelection(null);
        }}
        pending={isPending}
        llmConfigured={llmConfigured}
        validationError={validationError}
      />

      {activeAttempt && isPending ? (
        <p role="status" className="flex items-center gap-2 px-1 text-[12px] text-muted-foreground">
          <CircleDashed className="size-3.5 animate-spin" aria-hidden="true" />
          Running a bounded investigation for{" "}
          <span className="font-mono text-foreground">{activeAttempt.request.transaction_id}</span> with{" "}
          {activeAttempt.request.max_hops} hops, a {activeAttempt.request.time_window_seconds}s window and a{" "}
          {activeAttempt.request.result_limit}-record limit.
        </p>
      ) : null}

      {showingCached && activeResult ? (
        <p className="flex flex-wrap items-center gap-2 rounded-control border border-border bg-muted px-3 py-2 text-[11.5px] text-muted-foreground">
          Showing the last run from this tab:{" "}
          <span className="font-mono text-foreground">{activeResult.transaction_id}</span> ·{" "}
          {formatRelative(activeAttempt ? new Date(activeAttempt.startedAt).toISOString() : null)} ·
          the request that produced it is restored with the response.
        </p>
      ) : null}

      {error ? (
        <Card>
          <ErrorState
            error={error}
            retryLabel="Run again"
            onRetry={() => submit(form)}
            hint="A 503 means the service could not read a required artifact. Check System for the dependency it names."
          />
        </Card>
      ) : null}

      {activeResult && activeAttempt && model ? (
        <AgentWindow
          key={activeResult.investigation_id}
          response={activeResult}
          request={activeAttempt.request}
          model={model}
          selection={selection}
          onSelectionChange={setSelection}
          mode={mode}
          onModeChange={setMode}
          pathIndex={pathIndex}
          onPathIndexChange={setPathIndex}
          onWiden={widenBounds}
          widenDisabled={isPending}
        />
      ) : null}

      {!activeResult && !error && isPending ? (
        <Card>
          <CardHeader
            title="Waiting for the investigation response"
            description="Stages, graph and evidence table appear when the service answers."
            level={2}
          />
          <TableSkeleton rows={5} columns={4} />
        </Card>
      ) : null}

      {!activeResult && !error && !isPending ? (
        <Card>
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-5 py-3.5 [&::-webkit-details-marker]:hidden">
              <span className="text-[13px] font-medium text-foreground">How a run works</span>
              <span className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                Three steps. Nothing is drawn until an executed query returns it.
                <ChevronDown
                  className="size-3.5 transition-transform duration-[var(--motion-fast)] group-open:rotate-180 motion-reduce:transition-none"
                  aria-hidden="true"
                />
              </span>
            </summary>
            <div className="grid grid-cols-1 gap-4 border-t border-border px-5 py-4 md:grid-cols-3">
              <div>
                <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
                  <Search className="size-3.5" aria-hidden="true" />
                  1. Pick an anchor
                </p>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                  Use a transaction id from Alerts or Cases. The id must exist in the score artifact
                  and in the replay graph.
                </p>
              </div>
              <div>
                <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
                  <Play className="size-3.5" aria-hidden="true" />
                  2. Declare bounds
                </p>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                  Hops, time window and record limit bound the search. The service refuses wider
                  traversals than the declared limits.
                </p>
              </div>
              <div>
                <p className="flex items-center gap-1.5 text-[12.5px] font-medium text-foreground">
                  <CircleDashed className="size-3.5" aria-hidden="true" />
                  3. Read the evidence
                </p>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                  Every account, transfer and path shown was returned by an executed query, with its
                  own identifier and the stage that produced it.
                </p>
              </div>
            </div>
          </details>
        </Card>
      ) : null}

      {runLog}
    </div>
  );
}
