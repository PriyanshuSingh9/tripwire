/**
 * The eight-stage execution rail.
 *
 * Every stage reads a field of the investigation response. Where the API does
 * not report something (for example per-stage timings, an expansion of the
 * search, or a policy gate that was not requested), the stage says so instead
 * of implying success.
 */

import type { InvestigationRequest, InvestigationResponse } from "@/lib/api/types";
import { DENIAL_LABELS, LLM_FALLBACK_LABELS, TOOL_LABELS } from "@/components/ui/semantic";
import { formatLatency, formatNumber, formatScore } from "@/lib/format";
import type { GraphModel } from "@/lib/graph/model";

export type StageStatus = "completed" | "waiting" | "failed" | "not_run" | "unavailable";

export interface StageFact {
  label: string;
  value: string;
  tone?: "default" | "muted" | "warning" | "critical" | "safe";
}

export interface ExecutionStage {
  id: string;
  label: string;
  status: StageStatus;
  /** One sentence saying what this stage did. */
  summary: string;
  facts: StageFact[];
  caveat?: string;
}

export const STAGE_STATUS_LABEL: Record<StageStatus, string> = {
  completed: "Done",
  waiting: "Waiting",
  failed: "Failed",
  not_run: "Not run",
  unavailable: "Not reported",
};

function pendingStage(id: string, label: string): ExecutionStage {
  return {
    id,
    label,
    status: "waiting",
    summary: "Waiting for the API response.",
    facts: [],
  };
}

const STAGE_ORDER: { id: string; label: string }[] = [
  { id: "alert", label: "Alert" },
  { id: "context", label: "Context" },
  { id: "tool_selection", label: "Tool selection" },
  { id: "graph_query", label: "Graph query" },
  { id: "evidence", label: "Evidence" },
  { id: "verification", label: "Verification" },
  { id: "policy_gate", label: "Policy gate" },
  { id: "outcome", label: "Outcome" },
];

export function emptyRail(): ExecutionStage[] {
  return STAGE_ORDER.map((stage) => pendingStage(stage.id, stage.label));
}

export function buildExecutionRail(
  response: InvestigationResponse,
  request: InvestigationRequest,
  model: GraphModel,
): ExecutionStage[] {
  const trace = response.tool_trace;
  const report = response.report ?? null;
  const action = response.action ?? null;
  const anchorTransfer = model.transfers.find((row) => row.isAnchor)?.transaction ?? null;
  const failures = trace.filter((entry) => entry.status === "error");
  const truncated = trace.filter((entry) => entry.truncated);
  const perToolMs = trace.reduce((total, entry) => total + entry.duration_ms, 0);
  const modelOrigin = trace.filter((entry) => entry.origin === "model").length;

  const alertStage: ExecutionStage = {
    id: "alert",
    label: "Alert",
    status: "completed",
    summary: `The anchor transaction was found in the target-free score artifact and mapped to a policy decision by code.`,
    facts: [
      { label: "Anchor transaction", value: response.transaction_id },
      { label: "Risk score", value: formatScore(response.risk_score) },
      {
        label: "Policy decision",
        value: response.decision,
        tone: response.decision === "review" ? "warning" : "default",
      },
      { label: "Policy version", value: response.policy_version },
      { label: "Investigation id", value: response.investigation_id },
    ],
    caveat:
      "The score is a model estimate loaded from the replay artifact. The decision is deterministic policy, not a fraud finding.",
  };

  const contextStage: ExecutionStage = {
    id: "context",
    label: "Context",
    status: anchorTransfer ? "completed" : "unavailable",
    summary: anchorTransfer
      ? "The anchor transfer's account ends were returned, so bounded pattern queries could start from them."
      : "The anchor transfer record was not returned, so pattern queries could not be anchored.",
    facts: [
      { label: "Requested hops", value: String(request.max_hops) },
      { label: "Time window", value: `${formatNumber(request.time_window_seconds)} s` },
      { label: "Result limit", value: String(request.result_limit) },
      anchorTransfer
        ? { label: "Sender", value: anchorTransfer.source_account_key }
        : { label: "Sender", value: "not returned", tone: "muted" },
      anchorTransfer
        ? { label: "Receiver", value: anchorTransfer.destination_account_key }
        : { label: "Receiver", value: "not returned", tone: "muted" },
    ],
  };

  const toolSelectionStage: ExecutionStage = {
    id: "tool_selection",
    label: "Tool selection",
    status: trace.length === 0 ? "unavailable" : "completed",
    summary:
      trace.length === 0
        ? "The response carries no tool trace for this investigation."
        : `${trace.length} graph tool call${trace.length === 1 ? "" : "s"} were recorded; ${
            modelOrigin === 0 ? "all were selected by policy" : `${modelOrigin} were selected by the model`
          }.`,
    facts:
      trace.length === 0
        ? [{ label: "tool_trace", value: "empty", tone: "muted" }]
        : [
            { label: "Calls recorded", value: String(trace.length) },
            {
              label: "Selected by",
              value: modelOrigin === 0 ? "policy (fixed set)" : `${modelOrigin} model, ${trace.length - modelOrigin} policy`,
            },
            {
              label: "Transport",
              value: trace.some((entry) => entry.transport === "mcp") ? "MCP boundary" : "direct calls",
            },
          ],
    caveat:
      "The deterministic path always calls the same approved tools. Model-selected calls appear only when the request opted into the LLM agent.",
  };

  const graphQueryStage: ExecutionStage = {
    id: "graph_query",
    label: "Graph query",
    status:
      trace.length === 0
        ? "unavailable"
        : failures.length > 0
          ? "failed"
          : "completed",
    summary:
      trace.length === 0
        ? "No executed query is reported for this investigation."
        : failures.length > 0
          ? `${failures.length} of ${trace.length} bounded queries failed.`
          : `${trace.length} bounded parameterized queries completed against FalkorDB.`,
    facts:
      trace.length === 0
        ? [{ label: "Queries", value: "not reported", tone: "muted" }]
        : [
            { label: "Queries executed", value: String(trace.length) },
            { label: "Sum of tool durations", value: formatLatency(perToolMs) },
            {
              label: "Truncated results",
              value: truncated.length === 0 ? "none" : String(truncated.length),
              tone: truncated.length === 0 ? "default" : "warning",
            },
          ],
    caveat:
      "Tools run concurrently, so the sum of their durations can exceed the evidence-phase wall time. Per-stage timings are not exposed by the API.",
  };

  const evidenceStage: ExecutionStage = {
    id: "evidence",
    label: "Evidence",
    status: "completed",
    summary:
      response.evidence.transactions.length === 0 && response.evidence.accounts.length === 0
        ? "The executed queries returned no records inside the declared bounds."
        : "Only records returned by the executed queries are shown, each with its source identifier.",
    facts: [
      { label: "Transfers", value: formatNumber(response.evidence.transactions.length) },
      { label: "Accounts", value: formatNumber(response.evidence.accounts.length) },
      { label: "Paths", value: formatNumber(response.evidence.paths.length) },
      {
        label: "Evidence wall time",
        value: formatLatency(response.evidence.query_duration_ms),
      },
    ],
  };

  const llm = report?.llm ?? null;
  const verificationStage: ExecutionStage = {
    id: "verification",
    label: "Verification",
    status: report === null ? "not_run" : "completed",
    summary:
      report === null
        ? "No report accompanies this response, so nothing was verified."
        : llm?.status === "fallback"
          ? "The generated draft was rejected or unavailable, so the deterministic report was verified instead."
          : llm?.status === "completed"
            ? "Deterministic checks accepted every cited claim and path against the retrieved evidence."
            : "Deterministic checks accepted the report against the retrieved evidence.",
    facts:
      report === null
        ? [{ label: "report", value: "absent", tone: "muted" }]
        : [
            { label: "Report source", value: report.source },
            { label: "Validation", value: report.validation },
            { label: "Supporting references", value: String(report.supporting_transaction_ids.length) },
            {
              label: "Counterevidence references",
              value: String(report.counterevidence_ids.length),
            },
            ...(llm
              ? [
                  { label: "Provider", value: `${llm.provider} · ${llm.model}` },
                  { label: "Provider status", value: llm.status },
                  { label: "Rounds", value: String(llm.rounds) },
                  { label: "Tool calls", value: String(llm.tool_calls) },
                ]
              : []),
          ],
    caveat: llm?.fallback_reason ? LLM_FALLBACK_LABELS[llm.fallback_reason] : undefined,
  };

  const policyGateStage: ExecutionStage = {
    id: "policy_gate",
    label: "Policy gate",
    status: action === null ? "not_run" : action.status === "denied" ? "failed" : "completed",
    summary:
      action === null
        ? "This request did not opt into the controlled case action, so the gate did not run."
        : action.status === "no_action"
          ? "The gate ran and found no graph-supported action to take."
          : action.status === "denied"
            ? "The gate refused the proposed case action."
            : `The gate accepted a ${action.proposed_action.replace("_", " ")} proposed by the ${action.proposed_by}.`,
    facts:
      action === null
        ? [
            { label: "act", value: "false (not requested)", tone: "muted" },
            { label: "Gate", value: "did not run", tone: "muted" },
          ]
        : [
            { label: "Proposed", value: action.proposed_action },
            { label: "Proposed by", value: action.proposed_by },
            { label: "Gate status", value: action.status },
            ...(action.denial_code
              ? [{ label: "Denial code", value: action.denial_code, tone: "warning" as const }]
              : []),
            ...(action.case_id ? [{ label: "Case", value: action.case_id }] : []),
            { label: "Evidence references", value: String(action.evidence_refs.length) },
            { label: "Tool runs cited", value: String(action.tool_run_ids.length) },
          ],
    caveat: action?.denial_code
      ? DENIAL_LABELS[action.denial_code]
      : action !== null
        ? "The gate writes to the local case store only. It cannot change the score or the decision and never touches a payment."
        : undefined,
  };

  const outcomeStage: ExecutionStage = {
    id: "outcome",
    label: "Outcome",
    status: response.outcome === "evidence_found" ? "completed" : "not_run",
    summary:
      response.outcome === "evidence_found"
        ? "Executed queries returned at least one path or pattern inside the declared bounds."
        : response.outcome === "no_matching_path"
          ? "The graph was queried successfully and nothing matched within the declared bounds."
          : "A query hit its cap or did not complete, so the answer is inconclusive.",
    facts: [
      { label: "Outcome", value: response.outcome },
      { label: "Decision", value: response.decision, tone: response.decision === "review" ? "warning" : "default" },
      ...(action?.case_id ? [{ label: "Case opened / enriched", value: action.case_id }] : []),
    ],
    caveat:
      response.outcome === "no_matching_path"
        ? "Absence inside bounded queries is not evidence of safety, and it never produces an allow decision on its own."
        : undefined,
  };

  return [
    alertStage,
    contextStage,
    toolSelectionStage,
    graphQueryStage,
    evidenceStage,
    verificationStage,
    policyGateStage,
    outcomeStage,
  ];
}

/** Headline for the run: decision and outcome in one line. */
export function runHeadline(response: InvestigationResponse): string {
  const parts = [
    `${formatScore(response.risk_score)} risk`,
    response.decision === "review" ? "policy: review" : "policy: allow",
    response.outcome.replace(/_/g, " "),
  ];
  return parts.join(" · ");
}

export function toolLabel(tool: string): string {
  const base = tool.startsWith("detect_flow_patterns") ? "detect_flow_patterns" : tool;
  const [name] = base.split(".");
  const suffix = tool.includes(".") ? ` (${tool.split(".").slice(1).join(".")})` : "";
  return `${TOOL_LABELS[name ?? tool] ?? name ?? tool}${suffix}`;
}

export function confidenceNote(response: InvestigationResponse): string {
  const capped = response.tool_trace.some((entry) => entry.truncated);
  if (response.outcome === "inconclusive" || capped) {
    return "A bounded query reached its cap, so the graph may hold connections this run did not see.";
  }
  if (response.outcome === "no_matching_path") {
    return "Nothing matched inside the declared bounds. Absence within a bounded query is not proof of safety.";
  }
  return "Every claim below refers to a record an executed query returned.";
}
