import {
  Ban,
  CalendarClock,
  CheckCircle2,
  CircleDot,
  CircleSlash,
  FileSearch,
  FlaskConical,
  HelpCircle,
  Info,
  MinusCircle,
  PlugZap,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  TriangleAlert,
  XCircle,
} from "lucide-react";

import { StatusBadge, type BadgeTone } from "@/components/ui/badge";
import type {
  ActionDenialCode,
  CaseActionResult,
  CaseStatus,
  FalkorStatus,
  InvestigationOutcome,
  LlmFallbackReason,
  PolicyDecision,
  ToolRunOutcome,
} from "@/lib/api/types";

/* ---------------------------------------------------------------------------
 * Vocabulary
 *
 * One place decides how a domain value reads. Every surface uses these so the
 * same state never appears with two different words or two different colours.
 * Each entry carries label + icon + tone; colour is never the only signal.
 * ------------------------------------------------------------------------- */

export const DECISION_META: Record<
  PolicyDecision,
  { label: string; tone: BadgeTone; icon: typeof ShieldAlert; meaning: string }
> = {
  review: {
    label: "Review",
    tone: "warning",
    icon: ShieldAlert,
    meaning:
      "The deterministic policy maps this score to human review. It is a queue decision, not a fraud finding.",
  },
  allow: {
    label: "Allow",
    tone: "neutral",
    icon: ShieldCheck,
    meaning: "The score is below the locked validation threshold, so policy allows it.",
  },
};

export const OUTCOME_META: Record<
  InvestigationOutcome,
  { label: string; tone: BadgeTone; icon: typeof ShieldAlert; meaning: string }
> = {
  evidence_found: {
    label: "Evidence found",
    tone: "warning",
    icon: FileSearch,
    meaning: "Executed queries returned at least one path or pattern within the declared bounds.",
  },
  no_matching_path: {
    label: "No matching path",
    tone: "neutral",
    icon: CircleSlash,
    meaning:
      "The graph was queried successfully and nothing matched inside the declared bounds. This is not evidence of safety.",
  },
  inconclusive: {
    label: "Inconclusive",
    tone: "warning",
    icon: HelpCircle,
    meaning:
      "A query returned a capped result, so the search was cut short. Absence beyond the cap is unknown.",
  },
  unknown_anchor: {
    label: "Unknown anchor",
    tone: "critical",
    icon: XCircle,
    meaning: "The anchor identifier is not present in the graph.",
  },
};

export const TOOL_OUTCOME_META: Record<ToolRunOutcome, { label: string; tone: BadgeTone }> = {
  evidence_found: { label: "Evidence found", tone: "warning" },
  no_matching_path: { label: "No match in bounds", tone: "neutral" },
  inconclusive: { label: "Capped", tone: "warning" },
  unknown_anchor: { label: "Unknown anchor", tone: "critical" },
  invalid_input: { label: "Invalid input", tone: "critical" },
};

export const CASE_STATUS_META: Record<
  CaseStatus,
  { label: string; tone: BadgeTone; meaning: string }
> = {
  suspected: {
    label: "Suspected",
    tone: "warning",
    meaning: "Opened and awaiting an explicit analyst decision. Not a confirmed finding.",
  },
  under_review: {
    label: "Under review",
    tone: "primary",
    meaning: "Marked for review. No current transition produces this state.",
  },
  confirmed: {
    label: "Confirmed",
    tone: "critical",
    meaning: "An analyst explicitly confirmed the case. Stored state, not an automated verdict.",
  },
  dismissed: {
    label: "Dismissed",
    tone: "neutral",
    meaning: "An analyst dismissed the case. History and provenance are preserved.",
  },
};

export const FALKOR_META: Record<
  FalkorStatus,
  { label: string; tone: BadgeTone; icon: typeof PlugZap }
> = {
  available: { label: "Available", tone: "safe", icon: CheckCircle2 },
  unavailable: { label: "Unavailable", tone: "critical", icon: XCircle },
  not_configured: { label: "Not configured", tone: "neutral", icon: MinusCircle },
};

export const ACTION_STATUS_META: Record<
  CaseActionResult["status"],
  { label: string; tone: BadgeTone; icon: typeof Info }
> = {
  executed: { label: "Executed", tone: "safe", icon: CheckCircle2 },
  denied: { label: "Denied by gate", tone: "warning", icon: Ban },
  no_action: { label: "No action", tone: "neutral", icon: CircleDot },
};

export const DENIAL_LABELS: Record<ActionDenialCode, string> = {
  anchor_not_scored: "The anchor transaction is not in the score artifact.",
  outcome_not_actionable: "The investigation outcome is not actionable.",
  decision_not_review: "Opening a case requires a review decision.",
  evidence_refs_missing: "The proposal cited no evidence references.",
  evidence_ref_unverified: "A cited reference was not returned by this investigation's queries.",
  case_already_active: "The transaction already has an active case.",
  no_open_case: "Enrichment needs an existing open case.",
  duplicate_enrichment: "The same evidence references were already attached.",
};

export const LLM_FALLBACK_LABELS: Record<LlmFallbackReason, string> = {
  not_configured: "No provider is configured; the deterministic report was used.",
  provider_error: "The provider returned an error; the deterministic report was used.",
  timeout: "The provider timed out; the deterministic report was used.",
  invalid_output: "The provider output did not parse; the deterministic report was used.",
  verification_failed:
    "A cited claim or path did not match retrieved evidence, so the deterministic report was used.",
};

export const TOOL_LABELS: Record<string, string> = {
  trace_funds: "Fund tracing",
  inspect_counterparties: "Counterparty inspection",
  detect_flow_patterns: "Flow pattern detection",
};

export const DIRECTION_LABELS: Record<string, string> = {
  both: "Both directions",
  inbound: "Inbound",
  outbound: "Outbound",
  "pattern:fan_out": "Pattern: fan-out",
  "pattern:fan_in": "Pattern: fan-in",
  "pattern:pass_through": "Pattern: pass-through",
};

export const SLIPPED_STATE_ICON = FlaskConical;
export const CALENDAR_ICON = CalendarClock;
export const INFO_ICON = Info;
export const WARNING_ICON = TriangleAlert;
export const SHIELD_QUESTION_ICON = ShieldQuestion;

/* ---------------------------------------------------------------------------
 * Renders
 * ------------------------------------------------------------------------- */

export function DecisionBadge({ decision }: { decision: PolicyDecision }): React.JSX.Element {
  const meta = DECISION_META[decision];
  return (
    <StatusBadge label={meta.label} tone={meta.tone} icon={meta.icon} title={meta.meaning} />
  );
}

export function OutcomeBadge({
  outcome,
}: {
  outcome: InvestigationOutcome;
}): React.JSX.Element {
  const meta = OUTCOME_META[outcome];
  return (
    <StatusBadge label={meta.label} tone={meta.tone} icon={meta.icon} title={meta.meaning} />
  );
}

export function ToolOutcomeBadge({ outcome }: { outcome: ToolRunOutcome }): React.JSX.Element {
  const meta = TOOL_OUTCOME_META[outcome];
  return <StatusBadge label={meta.label} tone={meta.tone} />;
}

export function CaseStatusBadge({ status }: { status: CaseStatus }): React.JSX.Element {
  const meta = CASE_STATUS_META[status];
  return <StatusBadge label={meta.label} tone={meta.tone} title={meta.meaning} />;
}

export function FalkorBadge({ status }: { status: FalkorStatus }): React.JSX.Element {
  const meta = FALKOR_META[status];
  return <StatusBadge label={meta.label} tone={meta.tone} icon={meta.icon} announce />;
}

export function ActionStatusBadge({
  status,
}: {
  status: CaseActionResult["status"];
}): React.JSX.Element {
  const meta = ACTION_STATUS_META[status];
  return <StatusBadge label={meta.label} tone={meta.tone} icon={meta.icon} />;
}
