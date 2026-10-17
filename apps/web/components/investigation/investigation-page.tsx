"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ChevronDown, CircleDashed, SlidersHorizontal } from "lucide-react";

import { Card, CardBody, CardHeader, StatLine } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/badge";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { Disclosure } from "@/components/ui/side-panel";
import { DecisionBadge, OutcomeBadge } from "@/components/ui/semantic";
import { OpenCaseCard } from "@/components/cases/open-case-card";
import { ExecutionRail } from "@/components/graph/execution-rail";
import { GraphWorkspace } from "@/components/graph/graph-workspace";
import type { GraphMode } from "@/components/graph/graph-toolbar";
import type { GraphSelection } from "@/components/graph/graph-canvas";
import { RiskPanel } from "@/components/graph/risk-panel";
import {
  DEFAULT_RUN_FORM,
  RunForm,
  validateRunForm,
  type RunFormState,
} from "@/components/simulation/run-form";
import { useHealth } from "@/lib/api/hooks";
import type { InvestigationRequest } from "@/lib/api/types";
import { formatRelative, formatScore } from "@/lib/format";
import { buildGraphModel, transactionNodeId } from "@/lib/graph/model";
import { buildExecutionRail, emptyRail } from "@/lib/graph/stages";
import type { RunAttempt } from "@/lib/runs/use-investigation-run";
import { useLastRun } from "@/lib/runs/last-result";
import { useLoggedInvestigation } from "@/lib/runs/use-investigation-run";

/**
 * Guided investigation.
 *
 * Opened with `?transaction_id=`, which is how the alert queue and the case
 * list link here. The investigation starts on arrival with the default bounds;
 * the full pipeline form stays behind "Advanced options". The page presents
 * four answers in order: what triggered the alert, how the entities connect,
 * what the evidence shows, and what can happen next.
 */
export function GuidedInvestigationPage(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlTransactionId = searchParams.get("transaction_id") ?? "";

  const { run, result, attempt, error, isPending } = useLoggedInvestigation();
  const health = useHealth();
  const lastRun = useLastRun();

  const [form, setForm] = useState<RunFormState>({
    ...DEFAULT_RUN_FORM,
    transactionId: urlTransactionId,
  });
  const [validationError, setValidationError] = useState<string | null>(null);
  const [selection, setSelection] = useState<GraphSelection | null>(null);
  const [mode, setMode] = useState<GraphMode>("all");
  const [pathIndex, setPathIndex] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Results are shown only for the anchor named in the URL, so navigating
  // between alerts never mixes two transactions on one screen.
  const freshResult = result && result.transaction_id === urlTransactionId ? result : null;
  const cachedRun =
    lastRun && lastRun.request.transaction_id === urlTransactionId ? lastRun : null;
  const activeResult = freshResult ?? cachedRun?.response ?? null;
  const activeAttempt: RunAttempt | null =
    freshResult && attempt
      ? attempt
      : cachedRun
        ? { request: cachedRun.request, startedAt: Date.parse(cachedRun.at) }
        : null;
  const showingCached = freshResult === null && cachedRun !== null;

  const llmConfigured = health.data?.llm === "configured";
  const model = useMemo(
    () => (activeResult ? buildGraphModel(activeResult) : null),
    [activeResult],
  );

  const syncUrl = useCallback(
    (transactionId: string) => {
      router.replace(`/investigation?transaction_id=${encodeURIComponent(transactionId)}`, {
        scroll: false,
      });
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

  // The URL is the source of truth for the anchor; keep the form in step when
  // the user follows a link or the history.
  useEffect(() => {
    setForm((current) =>
      current.transactionId === urlTransactionId
        ? current
        : { ...current, transactionId: urlTransactionId },
    );
  }, [urlTransactionId]);

  // Start the investigation on arrival, once per anchor. The reference guard
  // survives renders and prevents a failed run from looping.
  const autoStartedRef = useRef<string | null>(null);
  useEffect(() => {
    if (urlTransactionId.length === 0 || isPending) return;
    if (freshResult || cachedRun) return;
    if (autoStartedRef.current === urlTransactionId) return;
    autoStartedRef.current = urlTransactionId;
    submit({ ...DEFAULT_RUN_FORM, transactionId: urlTransactionId });
  }, [urlTransactionId, isPending, freshResult, cachedRun, submit]);

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

  const rerunFromAttempt = useCallback(() => {
    if (!activeAttempt) return;
    const request = activeAttempt.request;
    const state: RunFormState = {
      transactionId: request.transaction_id,
      maxHops: String(request.max_hops),
      timeWindowSeconds: String(request.time_window_seconds),
      resultLimit: String(request.result_limit),
      useLlm: request.use_llm,
      act: request.act,
    };
    setForm(state);
    submit(state);
  }, [activeAttempt, submit]);

  const stages =
    activeResult && activeAttempt
      ? buildExecutionRail(activeResult, activeAttempt.request, buildGraphModel(activeResult))
      : emptyRail();

  const advancedForm = (
    <RunForm
      state={form}
      onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
      onSubmit={() => submit(form)}
      onReset={() => {
        setForm({ ...DEFAULT_RUN_FORM, transactionId: urlTransactionId });
        setValidationError(null);
      }}
      pending={isPending}
      llmConfigured={llmConfigured}
      validationError={validationError}
    />
  );

  return (
    <div className="space-y-4">
      {urlTransactionId.length > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link
            href="/alerts?decision=review"
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" />
            Back to alerts
          </Link>
          <Button
            size="sm"
            variant="secondary"
            icon={SlidersHorizontal}
            onClick={() => setAdvancedOpen((open) => !open)}
            aria-expanded={advancedOpen}
          >
            {advancedOpen ? "Hide advanced options" : "Advanced options"}
          </Button>
        </div>
      ) : null}

      {urlTransactionId.length === 0 ? (
        <Card>
          <CardHeader
            title="Start an investigation"
            description="Open an alert from the queue and it lands here ready to run, or name a transaction yourself."
            level={2}
            action={<Chip>manual entry</Chip>}
          />
          <CardBody>
            <p className="text-[12px] leading-relaxed text-muted-foreground">
              Alerts, cases and the overview all link into this workspace with the transaction
              already filled in. Nothing is drawn until an executed query returns it.
            </p>
            <div className="mt-3">
              <Link
                href="/alerts?decision=review"
                className="inline-flex items-center gap-1.5 rounded-control bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground transition-colors duration-[var(--motion-fast)] hover:bg-primary/90"
              >
                Review alerts
              </Link>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {advancedOpen ? advancedForm : null}
      {urlTransactionId.length === 0 && !advancedOpen ? advancedForm : null}

      {error ? (
        <Card>
          <ErrorState
            error={error}
            retryLabel="Run again"
            onRetry={() => submit(form)}
            hint="A 503 means the service could not read a required artifact; the error above names the dependency it could not read."
          />
        </Card>
      ) : null}

      {urlTransactionId.length > 0 && isPending && !activeResult ? (
        <Card>
          <CardHeader
            title="Reading the evidence"
            description="Bounded queries are running against the replay graph. Stages and records appear when the service answers."
            level={2}
          />
          <div className="space-y-3 px-5 py-4">
            <p role="status" className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
              <CircleDashed className="size-3.5 animate-spin" aria-hidden="true" />
              Investigating{" "}
              <span className="font-mono text-foreground">{urlTransactionId}</span> with the
              default bounds.
            </p>
            <TableSkeleton rows={4} columns={3} />
          </div>
        </Card>
      ) : null}

      {showingCached && activeResult ? (
        <div className="flex flex-wrap items-center gap-3 rounded-control border border-border bg-muted px-3 py-2 text-[11.5px] text-muted-foreground">
          <span>
            Showing the last investigation of this transaction from this tab,{" "}
            {formatRelative(activeAttempt ? new Date(activeAttempt.startedAt).toISOString() : null)}.
          </span>
          <Button size="sm" variant="secondary" onClick={rerunFromAttempt} disabled={isPending}>
            Run again
          </Button>
        </div>
      ) : null}

      {urlTransactionId.length > 0 ? (
        <Card>
          <CardHeader
            title="What triggered this alert"
            description="Flagged by the scoring model and set to review by the policy. The queue keeps no written reason, so the sections below show what the executed queries support."
            level={2}
            action={
              activeResult ? (
                <span className="flex flex-wrap items-center gap-2">
                  <DecisionBadge decision={activeResult.decision} />
                  <Chip mono>{formatScore(activeResult.risk_score)} risk score</Chip>
                </span>
              ) : null
            }
          />
          <CardBody>
            <dl className="space-y-1">
              <StatLine
                label="Transaction"
                value={<span className="font-mono text-[11.5px] break-all">{urlTransactionId}</span>}
              />
              <StatLine
                label="Status"
                value={
                  activeResult ? (
                    <DecisionBadge decision={activeResult.decision} />
                  ) : (
                    <span className="text-[12px] text-muted-foreground">reading from the service</span>
                  )
                }
              />
              <StatLine
                label="Risk score"
                value={
                  activeResult ? (
                    <span className="tabular text-[12px] text-foreground">
                      {formatScore(activeResult.risk_score)} (model estimate)
                    </span>
                  ) : (
                    <span className="text-[12px] text-muted-foreground">not read yet</span>
                  )
                }
              />
            </dl>
            <p className="pt-2 text-[12px] leading-relaxed text-muted-foreground">
              A review decision means the transaction scored above the policy threshold. It is a
              reason to look, not a finding: the graph evidence below says what is actually known.
            </p>
          </CardBody>
        </Card>
      ) : null}

      {activeResult && activeAttempt && model ? (
        <>
          <Card>
            <CardHeader
              title="How this ran"
              description="Eight stages, in order, from the request this browser sent to the response the service returned."
              level={2}
              action={
                <span className="flex flex-wrap items-center gap-2">
                  <OutcomeBadge outcome={activeResult.outcome} />
                  <Chip mono>{activeResult.investigation_id}</Chip>
                </span>
              }
            />
            <div className="px-5 py-4">
              <Disclosure summary="Show the stage detail">
                <div className="pt-2">
                  <ExecutionRail
                    stages={stages}
                    running={isPending}
                    runningLabel="A new run is in flight; these stages describe the previous response."
                  />
                </div>
              </Disclosure>
            </div>
          </Card>

          <GraphWorkspace
            response={activeResult}
            model={model}
            selection={selection}
            onSelectionChange={setSelection}
            mode={mode}
            onModeChange={setMode}
            pathIndex={pathIndex}
            onPathIndexChange={setPathIndex}
            onExpand={widenBounds}
            expandLabel="Look further"
            expandHint="Runs a new investigation with one more hop and a larger record limit. It does not expand the current result."
            expandDisabled={isPending}
            rail={
              <>
                <RiskPanel
                  response={activeResult}
                  model={model}
                  onSelectPath={(index) => {
                    setPathIndex(index);
                    setMode("path");
                  }}
                  onInspectTransaction={(transactionId) =>
                    setSelection({ kind: "node", id: transactionNodeId(transactionId) })
                  }
                />
                <OpenCaseCard
                  transactionId={activeResult.transaction_id}
                  caseId={activeResult.action?.case_id ?? null}
                />
              </>
            }
          />
        </>
      ) : null}

      {urlTransactionId.length > 0 && !activeResult && !error && !isPending ? (
        <Card>
          <CardHeader
            title="No result on screen"
            description="The investigation did not run, or its response was cleared."
            level={2}
          />
          <EmptyState
            title="Nothing to show yet"
            description="Run the investigation to read the evidence for this transaction."
            action={
              <Button size="sm" onClick={() => submit({ ...DEFAULT_RUN_FORM, transactionId: urlTransactionId })}>
                Run investigation
              </Button>
            }
          />
        </Card>
      ) : null}

      {urlTransactionId.length > 0 ? (
        <details className="group rounded-control border border-border bg-card">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-5 py-3 [&::-webkit-details-marker]:hidden">
            <span className="text-[12px] font-medium text-foreground">
              What each section means
            </span>
            <ChevronDown
              className="size-3.5 text-muted-foreground transition-transform duration-[var(--motion-fast)] group-open:rotate-180 motion-reduce:transition-none"
              aria-hidden="true"
            />
          </summary>
          <div className="grid grid-cols-1 gap-4 border-t border-border px-5 py-4 md:grid-cols-3">
            <div>
              <p className="text-[12px] font-medium text-foreground">Connections</p>
              <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                Accounts and transfers the queries returned, with the direction the data records.
                A shared account is a lead to check, not proof of wrongdoing.
              </p>
            </div>
            <div>
              <p className="text-[12px] font-medium text-foreground">Evidence</p>
              <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                Every record an executed query returned, with its identifier and the stage that
                produced it. Records were verified against the response before being shown.
              </p>
            </div>
            <div>
              <p className="text-[12px] font-medium text-foreground">Next action</p>
              <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                Opening a case records the suspicion for review. Only an analyst confirmation on
                the Cases page marks it confirmed.
              </p>
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
}
