"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import {
  Database,
  FileText,
  Gavel,
  Layers,
  Play,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import { Card, CardHeader, StatLine } from "@/components/ui/card";
import { Chip, StatusBadge } from "@/components/ui/badge";
import { Disclosure } from "@/components/ui/side-panel";
import {
  ActionStatusBadge,
  DecisionBadge,
  LLM_FALLBACK_LABELS,
  DENIAL_LABELS,
  OutcomeBadge,
  ToolOutcomeBadge,
} from "@/components/ui/semantic";
import { cn } from "@/lib/utils";
import { formatDuration, formatNumber, formatScore } from "@/lib/format";
import type { InvestigationRequest, InvestigationResponse } from "@/lib/api/types";
import {
  callResultSentence,
  callScopeSentence,
  type ReplayStep,
  type ReplayStepKind,
} from "@/lib/graph/replay";

const STEP_ICON: Record<ReplayStepKind, LucideIcon> = {
  request: Play,
  screening: ShieldAlert,
  call: Database,
  evidence: Layers,
  report: FileText,
  gate: Gavel,
};

function tokenText(value: number | null | undefined): string {
  return value === null || value === undefined ? "not reported" : formatNumber(value);
}

/**
 * The agent console: the recorded run as a chat-style timeline.
 *
 * Each message is one recorded step. The header line of a message is a real
 * button, so the replay cursor can be moved from the keyboard; the body
 * follows only once the cursor has reached the step. The console states what
 * the service actually reports: observable tool calls and their results.
 * Query text and internal model reasoning are not part of the response.
 */
export function AgentConsole({
  response,
  request,
  steps,
  cursor,
  onCursorChange,
  className,
}: {
  response: InvestigationResponse;
  request: InvestigationRequest;
  steps: ReplayStep[];
  cursor: number;
  onCursorChange: (index: number) => void;
  className?: string;
}): React.JSX.Element {
  const activeRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLOListElement | null>(null);

  // Keep the active message inside the console's own scroll area. The list,
  // not the page, scrolls, so autoplay never drags the viewport along.
  useEffect(() => {
    const item = activeRef.current;
    const list = listRef.current;
    if (!item || !list) return;
    const itemRect = item.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    if (itemRect.top < listRect.top) {
      list.scrollTop -= listRect.top - itemRect.top + 8;
    } else if (itemRect.bottom > listRect.bottom) {
      list.scrollTop += itemRect.bottom - listRect.bottom + 8;
    }
  }, [cursor]);

  const complete = cursor >= steps.length - 1;

  return (
    <Card className={cn("flex flex-col overflow-hidden", className)}>
      <CardHeader
        title="Agent activity"
        description="Replay of the completed run, in recorded order. The service reports observable tool calls and their results; query text and internal model reasoning are not part of the response."
        level={2}
        action={
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              label={complete ? "Replay complete" : "Replaying"}
              tone={complete ? "safe" : "primary"}
            />
            <Chip mono>
              {cursor + 1} / {steps.length}
            </Chip>
          </span>
        }
      />

      <ol
        ref={listRef}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3"
        aria-label="Recorded investigation steps"
      >
        {steps.map((step, index) => {
          const active = index === cursor;
          const reached = index <= cursor;
          const icon =
            step.kind === "screening"
              ? response.decision === "review"
                ? ShieldAlert
                : ShieldCheck
              : STEP_ICON[step.kind];
          const Icon = icon;

          if (step.kind === "request") {
            return (
              <li key={step.id} className="flex justify-end">
                <div
                  aria-current={active ? "step" : undefined}
                  className={cn(
                    "max-w-[88%] rounded-control border px-3 py-2.5 transition-colors duration-[var(--motion-fast)]",
                    active
                      ? "border-primary/45 bg-primary/12"
                      : "border-primary/20 bg-primary/6",
                  )}
                >
                  <button
                    ref={active ? activeRef : undefined}
                    type="button"
                    onClick={() => onCursorChange(index)}
                    className="flex w-full items-center gap-2 text-left"
                    aria-label={`Jump to step ${index + 1}: ${step.title}`}
                  >
                    <span className="text-[12px] font-medium text-foreground">{step.title}</span>
                  </button>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Chip mono>{request.transaction_id}</Chip>
                    <Chip>
                      {request.max_hops} hop{request.max_hops === 1 ? "" : "s"}
                    </Chip>
                    <Chip>window {formatNumber(request.time_window_seconds)} s</Chip>
                    <Chip>limit {formatNumber(request.result_limit)} records</Chip>
                    <Chip title="Whether the request opted into the model-selected tool calls">
                      {request.use_llm ? "LLM agent enabled" : "Deterministic pipeline"}
                    </Chip>
                    <Chip title="Whether the request opted into the controlled case action">
                      {request.act ? "Case gate enabled" : "Case gate not requested"}
                    </Chip>
                  </div>
                </div>
              </li>
            );
          }

          return (
            <li key={step.id}>
              <article
                aria-current={active ? "step" : undefined}
                className={cn(
                  "rounded-control border transition-colors duration-[var(--motion-fast)]",
                  active
                    ? "border-primary/45 bg-surface-active"
                    : "border-border bg-muted/60",
                  !reached && "opacity-55",
                )}
              >
                <button
                  ref={active ? activeRef : undefined}
                  type="button"
                  onClick={() => onCursorChange(index)}
                  className="flex w-full items-start gap-2.5 px-3 pt-2.5 text-left"
                  aria-label={`Jump to step ${index + 1}: ${step.title}${
                    reached ? "" : " (not reached yet)"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "flex size-6 shrink-0 items-center justify-center rounded-full border",
                      active
                        ? "border-primary/45 bg-primary/12 text-primary"
                        : "border-border-strong bg-surface-hover text-muted-foreground",
                    )}
                  >
                    <Icon className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[12.5px] font-medium text-foreground">
                        {step.title}
                      </span>
                      {step.call ? <ToolOutcomeBadge outcome={step.call.outcome} /> : null}
                      {step.call?.truncated ? (
                        <StatusBadge label="Capped" tone="warning" title="The query hit its record cap; the search was cut short." />
                      ) : null}
                    </span>
                    {step.call ? (
                      <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                        {callScopeSentence(step.call)}
                      </span>
                    ) : null}
                  </span>
                  <span className="shrink-0 pt-0.5 font-mono text-[10px] text-muted-foreground">
                    {index + 1}
                  </span>
                </button>

                {reached ? (
                  <div className="px-3 pb-3 pt-2 pl-[50px]">
                    <StepBody step={step} response={response} request={request} />
                  </div>
                ) : (
                  <p className="px-3 pb-3 pl-[50px] text-[11px] text-muted-foreground">
                    Not reached yet. Select the step or play the replay to read what it did.
                  </p>
                )}
              </article>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function StepBody({
  step,
  response,
  request,
}: {
  step: ReplayStep;
  response: InvestigationResponse;
  request: InvestigationRequest;
}): React.JSX.Element {
  if (step.kind === "call" && step.call) {
    const entry = step.call;
    return (
      <div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip
            title={
              entry.transport === "mcp"
                ? "The service invoked this tool across the MCP boundary"
                : "The service invoked this tool directly"
            }
          >
            {entry.transport === "mcp" ? "MCP boundary" : "Direct call"}
          </Chip>
          <Chip
            title={
              entry.origin === "model"
                ? "The language model chose this call from the approved tool set"
                : "The deterministic pipeline issued this call"
            }
          >
            {entry.origin === "model" ? "Agent-selected" : "Policy-selected"}
          </Chip>
          <Chip>{step.addIds.filter((id) => !id.startsWith("sent:") && !id.startsWith("received:")).length} new records drawn</Chip>
        </div>

        <p className="mt-2 font-mono text-[11px] leading-relaxed text-foreground">
          {callScopeSentence(entry)}
        </p>
        <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
          Returned {callResultSentence(entry)} · {formatDuration(entry.duration_ms)}
        </p>
        {entry.truncated ? (
          <p className="mt-1.5 border-l-2 border-warning/60 pl-2 text-[11.5px] leading-relaxed text-warning">
            The traversal hit its record cap, so connections beyond the cap were not seen. The
            result is bounded, not complete.
          </p>
        ) : null}
        {entry.status === "error" ? (
          <p className="mt-1.5 border-l-2 border-critical/60 pl-2 text-[11.5px] leading-relaxed text-critical">
            The query failed. The investigation continued with the calls that succeeded.
          </p>
        ) : null}

        <Disclosure summary="Reported call detail" className="mt-2.5">
          <dl className="space-y-1">
            <StatLine label="tool" value={entry.tool} />
            <StatLine label="tool_run_id" value={entry.tool_run_id} />
            <StatLine label="transport" value={entry.transport} />
            <StatLine label="origin" value={entry.origin} />
            <StatLine label="status" value={entry.status} />
            <StatLine label="duration_ms" value={String(Math.round(entry.duration_ms))} />
            <StatLine label="result_count" value={String(entry.result_count)} />
            <StatLine label="path_count" value={String(entry.path_count)} />
            <StatLine label="truncated" value={String(entry.truncated)} />
          </dl>
          <p className="mt-2.5 text-[11px] text-muted-foreground">
            Arguments exactly as the service recorded them:
          </p>
          <pre className="mt-1 overflow-x-auto rounded-control border border-border bg-[#0a0b0b] px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-muted-foreground">
            {JSON.stringify(entry.arguments, null, 2)}
          </pre>
        </Disclosure>
      </div>
    );
  }

  if (step.kind === "screening") {
    return (
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <DecisionBadge decision={response.decision} />
          <OutcomeBadge outcome={response.outcome} />
          <Chip mono>{formatScore(response.risk_score)} risk</Chip>
          <Chip mono title="Deterministic policy version that mapped the score">
            {response.policy_version}
          </Chip>
        </div>
        <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
          The score is a model estimate loaded from the replay artifact. The decision is
          deterministic policy: it maps the score to review or allow, and it is a queue decision,
          not a fraud finding. The anchor transfer and its account ends are drawn on the canvas.
        </p>
        <dl className="mt-2 space-y-1">
          <StatLine label="Investigation id" value={response.investigation_id} />
          <StatLine label="Created" value={response.created_at} />
        </dl>
      </div>
    );
  }

  if (step.kind === "evidence") {
    return (
      <p className="text-[11.5px] leading-relaxed text-muted-foreground">
        {step.addIds.length} graph element{step.addIds.length === 1 ? "" : "s"} arrive here:
        records the attribution could not pin to a single recorded call, for example records
        beyond a capped traversal. The drawn graph now equals the full response, and the replay
        never draws a record the service did not return.
      </p>
    );
  }

  if (step.kind === "report") {
    const report = response.report ?? null;
    if (report === null) {
      return (
        <p className="text-[11.5px] text-muted-foreground">
          No report accompanies this response, so there is nothing to cite.
        </p>
      );
    }
    const llm = report.llm ?? null;
    return (
      <div>
        <p className="text-[12px] leading-relaxed text-foreground">{report.hypothesis}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StatusBadge
            label={report.validation === "validated" ? "Validated against evidence" : report.validation}
            tone="safe"
            icon={FileText}
          />
          <Chip>
            {report.source === "llm" ? "Language model draft, verified" : "Deterministic template"}
          </Chip>
          <Chip>recommendation: {report.recommendation}</Chip>
          <Chip>
            {report.cited_claims.length} cited transfer
            {report.cited_claims.length === 1 ? "" : "s"}
          </Chip>
        </div>
        {llm ? (
          <dl className="mt-2 space-y-1">
            <StatLine label="Provider" value={`${llm.provider} · ${llm.model}`} />
            <StatLine label="Provider status" value={llm.status} />
            <StatLine label="Rounds" value={String(llm.rounds)} />
            <StatLine label="Tool calls chosen" value={String(llm.tool_calls)} />
            <StatLine
              label="Tokens in / out"
              value={`${tokenText(llm.input_tokens)} / ${tokenText(llm.output_tokens)}`}
            />
            <StatLine label="Provider duration" value={formatDuration(llm.duration_ms)} />
          </dl>
        ) : (
          <p className="mt-2 text-[11.5px] text-muted-foreground">
            No provider call was made for this report; it was assembled by deterministic code.
          </p>
        )}
        {llm?.fallback_reason ? (
          <p className="mt-2 border-l-2 border-warning/60 pl-2 text-[11.5px] leading-relaxed text-warning">
            {LLM_FALLBACK_LABELS[llm.fallback_reason]}
          </p>
        ) : null}
      </div>
    );
  }

  // Gate
  const action = response.action ?? null;
  if (action === null) {
    return (
      <p className="text-[11.5px] leading-relaxed text-muted-foreground">
        Not run: this request did not opt into the controlled case action
        {request.act ? "." : " (the case gate stayed off)."}
      </p>
    );
  }
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        <ActionStatusBadge status={action.status} />
        <Chip>proposed: {action.proposed_action}</Chip>
        <Chip>by {action.proposed_by}</Chip>
      </div>
      <dl className="mt-2 space-y-1">
        <StatLine label="Reason" value={action.reason} />
        {action.denial_code ? (
          <StatLine label="Denial" value={DENIAL_LABELS[action.denial_code]} tone="warning" />
        ) : null}
        {action.case_id ? (
          <StatLine
            label="Case"
            value={
              <Link
                href={`/cases?case_id=${encodeURIComponent(action.case_id)}`}
                className="text-primary hover:underline"
              >
                {action.case_id}
              </Link>
            }
          />
        ) : null}
        <StatLine label="Evidence references" value={String(action.evidence_refs.length)} />
        <StatLine label="Tool runs cited" value={String(action.tool_run_ids.length)} />
      </dl>
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
        The gate writes to the local case store only. It cannot change the score or the decision,
        and it never touches a payment.
      </p>
    </div>
  );
}
