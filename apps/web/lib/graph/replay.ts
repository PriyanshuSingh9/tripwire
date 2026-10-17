/**
 * Replay plan for the agent window.
 *
 * The service answers one investigation request with one completed response.
 * This module turns that response into the ordered steps an analyst watches:
 * the request and its declared bounds, the screening result, every recorded
 * graph tool call in trace order with the records the response attributes to
 * it, any records the attribution cannot pin to a call, the citation-checked
 * report, and the controlled case gate.
 *
 * Attribution follows values the response itself carries:
 * - `detect_flow_patterns` calls name a flow pattern and an account; the
 *   paths whose direction is `pattern:<name>` and which include that account
 *   belong to the call.
 * - `trace_funds` calls return traversal paths (`both`, `inbound`,
 *   `outbound`) within the requested hop depth.
 * - record-producing calls without paths (`inspect_counterparties`) return the
 *   records that are not on any returned path.
 * Anything left arrives at the evidence step, so at the end of the replay the
 * drawn graph equals the full response. The replay never draws a record the
 * response did not return, and it never claims a query the service did not
 * record.
 */

import type { InvestigationResponse, ToolTraceEntry } from "@/lib/api/types";
import {
  accountNodeId,
  elementIdsForPath,
  receivedEdgeId,
  sentEdgeId,
  transactionNodeId,
  type GraphModel,
} from "@/lib/graph/model";
import { DIRECTION_LABELS, TOOL_LABELS } from "@/components/ui/semantic";
import { formatNumber } from "@/lib/format";

export type ReplayStepKind =
  | "request"
  | "screening"
  | "call"
  | "evidence"
  | "report"
  | "gate";

export interface ReplayStep {
  id: string;
  kind: ReplayStepKind;
  /** Short label for the timeline and the replay caption. */
  title: string;
  /** The recorded call, present on `call` steps. */
  call?: ToolTraceEntry;
  /** Graph ids this step draws for the first time. */
  addIds: string[];
  /** Graph ids this step highlights while it is the cursor. */
  highlightIds: string[];
}

function patternOf(entry: ToolTraceEntry): string | null {
  const value = entry.arguments.flow_pattern;
  return typeof value === "string" ? value : null;
}

function windowTail(entry: ToolTraceEntry): string {
  const window = entry.arguments.time_window_seconds;
  const limit = entry.arguments.result_limit;
  const parts: string[] = [];
  if (typeof window === "number") parts.push(`window ${formatNumber(window)} s`);
  if (typeof limit === "number") parts.push(`limit ${formatNumber(limit)} records`);
  return parts.length > 0 ? ` · ${parts.join(" · ")}` : "";
}

/** One plain-language sentence describing the call's real parameters. */
export function callScopeSentence(entry: ToolTraceEntry): string {
  const tail = windowTail(entry);
  if (entry.tool === "trace_funds") {
    const transaction =
      typeof entry.arguments.transaction_id === "string"
        ? entry.arguments.transaction_id
        : "the anchor transaction";
    const hops =
      typeof entry.arguments.max_hops === "number"
        ? `up to ${entry.arguments.max_hops} hop${entry.arguments.max_hops === 1 ? "" : "s"}`
        : "along returned paths";
    const direction =
      typeof entry.arguments.direction === "string"
        ? ` · ${DIRECTION_LABELS[entry.arguments.direction] ?? entry.arguments.direction}`
        : "";
    return `Trace funds from ${transaction}, ${hops}${direction}${tail}`;
  }
  if (entry.tool === "inspect_counterparties") {
    const transaction =
      typeof entry.arguments.transaction_id === "string"
        ? entry.arguments.transaction_id
        : "the anchor transaction";
    return `Inspect counterparties of ${transaction}${tail}`;
  }
  const pattern = patternOf(entry)?.replace(/_/g, "-") ?? "flow";
  const account =
    typeof entry.arguments.account_key === "string" ? entry.arguments.account_key : "the anchor";
  return `Detect ${pattern} flow on ${account}${tail}`;
}

/** One plain-language sentence describing what the call returned. */
export function callResultSentence(entry: ToolTraceEntry): string {
  const parts: string[] = [];
  if (entry.path_count > 0) {
    parts.push(`${entry.path_count} path${entry.path_count === 1 ? "" : "s"}`);
  }
  if (entry.result_count > 0) {
    parts.push(`${entry.result_count} record${entry.result_count === 1 ? "" : "s"}`);
  }
  if (parts.length === 0) {
    parts.push(entry.status === "error" ? "the query failed" : "no records inside the bounds");
  }
  if (entry.truncated) parts.push("capped at the limit");
  return parts.join(" · ");
}

export function callTitle(entry: ToolTraceEntry): string {
  const base = TOOL_LABELS[entry.tool] ?? entry.tool;
  const pattern = patternOf(entry);
  return pattern ? `${base} · ${pattern.replace(/_/g, "-")}` : base;
}

export interface ReplaySummary {
  calls: number;
  agentCalls: number;
  policyCalls: number;
  mcpCalls: number;
  truncatedCalls: number;
  failedCalls: number;
  toolDurationMs: number;
}

export function replaySummary(response: InvestigationResponse): ReplaySummary {
  const calls = response.tool_trace;
  return {
    calls: calls.length,
    agentCalls: calls.filter((entry) => entry.origin === "model").length,
    policyCalls: calls.filter((entry) => entry.origin === "policy").length,
    mcpCalls: calls.filter((entry) => entry.transport === "mcp").length,
    truncatedCalls: calls.filter((entry) => entry.truncated).length,
    failedCalls: calls.filter((entry) => entry.status === "error").length,
    toolDurationMs: calls.reduce((total, entry) => total + entry.duration_ms, 0),
  };
}

/** Build the ordered replay steps for one completed response. */
export function buildReplaySteps(
  response: InvestigationResponse,
  model: GraphModel,
): ReplayStep[] {
  const steps: ReplayStep[] = [];
  const drawn = new Set<string>();

  const exists = (id: string): boolean => model.nodeById.has(id) || model.edgeById.has(id);

  const draw = (ids: Iterable<string>): string[] => {
    const added: string[] = [];
    for (const id of ids) {
      if (drawn.has(id) || !exists(id)) continue;
      drawn.add(id);
      added.push(id);
    }
    return added;
  };

  steps.push({
    id: "request",
    kind: "request",
    title: "Run requested",
    addIds: [],
    highlightIds: [],
  });

  const anchor = model.transfers.find((row) => row.isAnchor);
  const anchorIds = anchor
    ? [
        transactionNodeId(anchor.id),
        sentEdgeId(anchor.id),
        receivedEdgeId(anchor.id),
        accountNodeId(anchor.transaction.source_account_key),
        accountNodeId(anchor.transaction.destination_account_key),
      ].filter(exists)
    : [];
  steps.push({
    id: "screening",
    kind: "screening",
    title: "Screening and policy",
    addIds: draw(anchorIds),
    highlightIds: anchorIds,
  });

  const claimedPaths = new Set<number>();

  for (const entry of response.tool_trace) {
    const highlight = new Set<string>();

    if (entry.tool === "detect_flow_patterns" && entry.path_count > 0) {
      const pattern = patternOf(entry);
      const account =
        typeof entry.arguments.account_key === "string" ? entry.arguments.account_key : null;
      let remaining = entry.path_count;
      model.paths.forEach((path, index) => {
        if (remaining <= 0 || claimedPaths.has(index)) return;
        if (pattern !== null && path.direction !== `pattern:${pattern}`) return;
        if (account !== null && !path.account_keys.includes(account)) return;
        claimedPaths.add(index);
        remaining -= 1;
        for (const id of elementIdsForPath(model, index)) highlight.add(id);
      });
    } else if (entry.tool === "trace_funds" && entry.path_count > 0) {
      const direction =
        typeof entry.arguments.direction === "string" ? entry.arguments.direction : "both";
      const maxHops =
        typeof entry.arguments.max_hops === "number" ? entry.arguments.max_hops : null;
      let remaining = entry.path_count;
      model.paths.forEach((path, index) => {
        if (remaining <= 0 || claimedPaths.has(index)) return;
        if (path.direction !== direction) return;
        if (maxHops !== null && path.depth > maxHops) return;
        claimedPaths.add(index);
        remaining -= 1;
        for (const id of elementIdsForPath(model, index)) highlight.add(id);
      });
    } else if (entry.path_count === 0 && entry.result_count > 0) {
      // Record-producing call without paths: the records that are not on any
      // returned path, in evidence order, up to the reported count.
      let remaining = entry.result_count;
      for (const row of model.transfers) {
        if (remaining <= 0) break;
        if (row.onPath || highlight.has(transactionNodeId(row.id))) continue;
        remaining -= 1;
        highlight.add(transactionNodeId(row.id));
        highlight.add(sentEdgeId(row.id));
        highlight.add(receivedEdgeId(row.id));
        highlight.add(accountNodeId(row.transaction.source_account_key));
        highlight.add(accountNodeId(row.transaction.destination_account_key));
      }
    }

    const highlightIds = [...highlight].filter(exists);
    steps.push({
      id: entry.tool_run_id,
      kind: "call",
      title: callTitle(entry),
      call: entry,
      addIds: draw(highlightIds),
      highlightIds,
    });
  }

  const leftover: string[] = [];
  for (const node of model.nodes) if (!drawn.has(node.id)) leftover.push(node.id);
  for (const edge of model.edges) if (!drawn.has(edge.id)) leftover.push(edge.id);
  if (leftover.length > 0) {
    steps.push({
      id: "evidence",
      kind: "evidence",
      title: "Evidence assembled",
      addIds: draw(leftover),
      highlightIds: [],
    });
  }

  const report = response.report ?? null;
  if (report !== null) {
    steps.push({
      id: "report",
      kind: "report",
      title: report.source === "llm" ? "Agent report" : "Deterministic report",
      addIds: [],
      highlightIds: report.supporting_transaction_ids
        .map((id) => transactionNodeId(id))
        .filter(exists),
    });
  }

  const action = response.action ?? null;
  steps.push({
    id: "gate",
    kind: "gate",
    title: "Policy gate",
    addIds: [],
    highlightIds: action
      ? action.evidence_refs.map((id) => transactionNodeId(id)).filter(exists)
      : [],
  });

  return steps;
}

/** Human label for a step, used by the replay controls. */
export function stepCaption(step: ReplayStep | null): string {
  if (step === null) return "No recorded steps.";
  if (step.kind === "call" && step.call) {
    return `${step.title} · ${callResultSentence(step.call)}`;
  }
  return step.title;
}
