"use client";

import {
  Ban,
  Bot,
  CircleDot,
  FileText,
  FolderPlus,
  Gavel,
  ScanSearch,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Chip, StatusBadge, type BadgeTone } from "@/components/ui/badge";
import {
  ActionStatusBadge,
  DECISION_META,
  DecisionBadge,
  DENIAL_LABELS,
  LLM_FALLBACK_LABELS,
  OutcomeBadge,
} from "@/components/ui/semantic";
import { cn } from "@/lib/utils";
import { formatDuration, formatNumber, formatScore } from "@/lib/format";
import type { InvestigationResponse } from "@/lib/api/types";
import { replaySummary } from "@/lib/graph/replay";

interface TerminalState {
  label: string;
  tone: BadgeTone;
  icon: LucideIcon;
  note: string;
}

function terminalState(response: InvestigationResponse): TerminalState {
  const action = response.action ?? null;
  if (response.decision === "allow") {
    return {
      label: "ALLOW",
      tone: "neutral",
      icon: ShieldCheck,
      note: "The score is below the review threshold, so policy allows it and the escalation chain stops at the decision.",
    };
  }
  if (action?.status === "executed" && action.proposed_action === "open_case") {
    return {
      label: "REVIEW · case opened",
      tone: "warning",
      icon: FolderPlus,
      note: "A suspected case is recorded and awaits an explicit analyst decision. It is not a finding.",
    };
  }
  if (action?.status === "executed" && action.proposed_action === "enrich_case") {
    return {
      label: "REVIEW · case enriched",
      tone: "warning",
      icon: FolderPlus,
      note: "The evidence references were attached to the existing case. The case state itself is unchanged.",
    };
  }
  if (action?.status === "denied") {
    return {
      label: "REVIEW · case change denied",
      tone: "warning",
      icon: Ban,
      note:
        action.denial_code !== null && action.denial_code !== undefined
          ? DENIAL_LABELS[action.denial_code]
          : "The gate refused the proposed case action.",
    };
  }
  if (action?.status === "no_action") {
    return {
      label: "REVIEW · nothing to act on",
      tone: "neutral",
      icon: CircleDot,
      note: "The gate ran and found no graph-supported case action for this outcome.",
    };
  }
  return {
    label: "REVIEW · analyst queue",
    tone: "warning",
    icon: ShieldAlert,
    note: "The score maps to human review. This run did not change any case.",
  };
}

interface Station {
  id: string;
  icon: LucideIcon;
  title: string;
  badge?: React.ReactNode;
  detail: React.ReactNode;
  emphasis?: boolean;
  warning?: string;
}

/**
 * The recorded escalation chain for one run, drawn as a vertical flow:
 * screening, the policy decision, the agent's bounded investigation, the
 * verified report, and the controlled case gate, ending in the terminal
 * rating. Every station reads a field of the response; none of it is
 * narrative colour.
 */
export function EscalationCard({
  response,
  className,
}: {
  response: InvestigationResponse;
  className?: string;
}): React.JSX.Element {
  const report = response.report ?? null;
  const llm = report?.llm ?? null;
  const action = response.action ?? null;
  const summary = replaySummary(response);
  const terminal = terminalState(response);
  const TerminalIcon = terminal.icon;

  const stations: Station[] = [
    {
      id: "screened",
      icon: ScanSearch,
      title: "Screened",
      badge: <Chip mono>{formatScore(response.risk_score)} risk</Chip>,
      detail: (
        <>
          The anchor transfer is present in the target-free score artifact. Policy version{" "}
          <span className="font-mono text-foreground">{response.policy_version}</span> mapped the
          estimate to a decision by code.
        </>
      ),
    },
    {
      id: "decision",
      icon: response.decision === "review" ? ShieldAlert : ShieldCheck,
      title: "Policy decision",
      badge: <DecisionBadge decision={response.decision} />,
      detail: DECISION_META[response.decision].meaning,
    },
    {
      id: "investigation",
      icon: Bot,
      title: "Bounded graph investigation",
      badge: (
        <Chip mono>
          {summary.calls} recorded call{summary.calls === 1 ? "" : "s"}
        </Chip>
      ),
      emphasis: true,
      detail: (
        <>
          {summary.calls} tool call{summary.calls === 1 ? "" : "s"} ran against FalkorDB;{" "}
          {summary.agentCalls > 0
            ? `${summary.agentCalls} were chosen by the language model and ${summary.policyCalls} by policy`
            : "all were issued by the deterministic pipeline"}
          . Transport:{" "}
          {summary.mcpCalls > 0
            ? `${summary.mcpCalls} of ${summary.calls} across the MCP boundary`
            : "direct calls"}
          . Total tool time {formatDuration(summary.toolDurationMs)}. The agent cannot widen the
          declared bounds.
        </>
      ),
      warning:
        summary.truncatedCalls > 0
          ? `${summary.truncatedCalls} call${
              summary.truncatedCalls === 1 ? "" : "s"
            } hit the record cap, so the graph may hold connections this run did not see.`
          : undefined,
    },
    {
      id: "report",
      icon: FileText,
      title: report?.source === "llm" ? "Agent report, checked" : "Deterministic report, checked",
      badge: report ? (
        <StatusBadge
          label={report.validation === "validated" ? "Validated" : report.validation}
          tone="safe"
          icon={FileText}
        />
      ) : undefined,
      detail: report
        ? llm
          ? `${llm.provider} · ${llm.model} · ${llm.rounds} round${
              llm.rounds === 1 ? "" : "s"
            } · ${llm.tool_calls} tool call${llm.tool_calls === 1 ? "" : "s"} · ${formatDuration(
              llm.duration_ms,
            )}. A deterministic check accepted every cited claim and path against retrieved evidence.`
          : "Assembled by deterministic code from the retrieved evidence. Every citation was checked against returned records."
        : "No report accompanies this response.",
      warning: llm?.fallback_reason ? LLM_FALLBACK_LABELS[llm.fallback_reason] : undefined,
    },
    {
      id: "gate",
      icon: Gavel,
      title: "Controlled case gate",
      badge: action ? (
        <ActionStatusBadge status={action.status} />
      ) : (
        <Chip>Not requested</Chip>
      ),
      detail:
        action !== null ? (
          <>
            <span className="font-mono text-foreground">{action.proposed_action}</span> proposed by{" "}
            {action.proposed_by}.{" "}
            {action.denial_code
              ? DENIAL_LABELS[action.denial_code]
              : action.status === "executed"
                ? `Accepted; the gate wrote to the local case store${
                    action.case_id ? ` (${action.case_id})` : ""
                  }.`
                : action.reason}
          </>
        ) : (
          "This run did not opt into the controlled case action, so the gate did not run. The decision and the score are unchanged either way."
        ),
    },
  ];

  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader
        title="Where this transaction stands"
        description="The recorded escalation chain for this run, end to end."
        level={2}
        action={
          <span className="flex flex-wrap items-center gap-1.5">
            <DecisionBadge decision={response.decision} />
            <OutcomeBadge outcome={response.outcome} />
          </span>
        }
      />

      <div className="flex-1 px-4 py-4">
        <ol className="relative space-y-3 pl-7">
          <span
            aria-hidden="true"
            className="absolute top-3 bottom-3 left-[11px] w-px bg-border"
          />
          {stations.map((station) => {
            const Icon = station.icon;
            return (
              <li key={station.id} className="relative">
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute top-0 -left-7 flex size-6 items-center justify-center rounded-full border",
                    station.emphasis
                      ? "border-primary/45 bg-primary/12 text-primary"
                      : "border-border-strong bg-surface-hover text-muted-foreground",
                  )}
                >
                  <Icon className="size-3.5" />
                </span>
                <div
                  className={cn(
                    "rounded-control border px-3 py-2.5",
                    station.emphasis
                      ? "border-primary/40 bg-primary/8"
                      : "border-border bg-muted/50",
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-[12.5px] font-medium text-foreground">{station.title}</p>
                    {station.badge}
                  </div>
                  <div className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                    {station.detail}
                  </div>
                  {station.warning ? (
                    <p className="mt-1.5 border-l-2 border-warning/60 pl-2 text-[11px] leading-relaxed text-warning">
                      {station.warning}
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>

        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-control border border-border bg-muted px-3 py-2.5">
          <span className="text-[10.5px] tracking-[0.03em] text-muted-foreground uppercase">
            Rating
          </span>
          <StatusBadge label={terminal.label} tone={terminal.tone} icon={TerminalIcon} />
          <span className="text-[11.5px] leading-relaxed text-muted-foreground">
            {terminal.note}
          </span>
        </div>

        <p className="mt-2.5 text-[11px] leading-relaxed text-muted-foreground">
          Escalation only: the agent adds scrutiny. It cannot change the score, the policy decision,
          or any payment.
        </p>

        {action !== null && summary.calls > 0 ? (
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            {formatNumber(action.evidence_refs.length)} evidence reference
            {action.evidence_refs.length === 1 ? "" : "s"} and{" "}
            {formatNumber(action.tool_run_ids.length)} recorded tool run
            {action.tool_run_ids.length === 1 ? "" : "s"} were cited by the gate.
          </p>
        ) : null}
      </div>
    </Card>
  );
}
