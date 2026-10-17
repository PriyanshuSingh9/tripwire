/**
 * TypeScript mirrors of the FastAPI response contracts in
 * `services/api/src/api/schemas.py` and `services/api/src/api/cases/schemas.py`.
 *
 * Every model in the API forbids extra fields, so these types are exact: a
 * field that is not declared here is not returned by the service. Fields the
 * API does not expose (target labels, typology truth) have no representation
 * on purpose.
 */

export type FalkorStatus = "available" | "unavailable" | "not_configured";
export type LlmStatus = "configured" | "not_configured";

export interface HealthResponse {
  status: "ok";
  falkordb: FalkorStatus;
  version: string;
  llm: LlmStatus;
}

export type PolicyDecision = "allow" | "review";

export interface AlertItem {
  transaction_id: string;
  timestamp: string;
  score: number;
  decision: PolicyDecision;
}

export interface AlertsResponse {
  items: AlertItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface SplitMetrics {
  rows: number;
  prevalence: number;
  alerts: number;
  precision: number;
  recall: number;
}

export interface ThresholdMetrics {
  precision: number;
  recall: number;
  f1: number;
  true_positives: number;
  false_positives: number;
}

export interface BudgetMetrics {
  budget: number;
  alerts: number;
  true_positives: number;
  precision: number;
  recall: number;
}

export interface ModelBenchmark {
  average_precision: Record<string, number>;
  val_threshold_max_f1: number;
  test_at_val_threshold: ThresholdMetrics;
  test_at_alert_budget: BudgetMetrics;
  splits: Record<string, SplitMetrics>;
}

export interface LatencyStats {
  samples: number;
  p50_ms: number;
  p95_ms: number;
  mean_ms: number;
  p99_ms?: number | null;
}

export interface SerialLatencyStats extends LatencyStats {
  serial_requests_per_second: number;
}

export interface CohortInferenceStats {
  rows: number;
  repeats: number;
  median_seconds: number;
  transactions_per_second: number;
  threads: number;
  max_abs_error_vs_saved_scores: number;
}

export interface ModelPerformance {
  single_prepared_row: LatencyStats;
  full_test_cohort: CohortInferenceStats;
  full_test_cohort_all_threads?: CohortInferenceStats | null;
  batches?: Record<string, LatencyStats> | null;
}

export interface InvestigationPerformance extends SerialLatencyStats {
  graph_query_sum: LatencyStats;
  evidence_transactions: number;
  evidence_accounts: number;
  evidence_paths: number;
  all_reports_validated: boolean;
}

export interface FlowQueryPerformance extends LatencyStats {
  matches_min: number;
  matches_max: number;
}

export interface GraphQueryPerformance {
  trace_funds: LatencyStats;
  inspect_counterparties: LatencyStats;
  fan_out: FlowQueryPerformance;
  fan_in: FlowQueryPerformance;
  pass_through: FlowQueryPerformance;
}

export interface CostStatus {
  external_llm_calls: number;
  external_llm_cost: string;
  local_compute_cost: string;
  cost_per_1000_decisions: string;
  dollar_weighted_recall: string;
  price_basis?: string | null;
  scoring_usd_per_1000_transactions?: number | null;
  scoring_cpu_seconds_per_1000_transactions?: number | null;
  investigation_usd?: number | null;
  investigation_cpu_seconds?: number | null;
  investigation_provisioned_usd?: number | null;
}

export interface CapacityLevel {
  concurrency: number;
  requests: number;
  throughput_rps: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  error_rate: number;
}

export interface CapacitySweep {
  endpoint: string;
  workers: number;
  duration_seconds: number;
  levels: CapacityLevel[];
  knee_concurrency?: number | null;
  peak_throughput_rps: number;
  first_error_concurrency?: number | null;
}

export interface ConcurrencyPerformance {
  falkordb_thread_count?: number | null;
  falkordb_max_queued_queries?: number | null;
  sweeps: Record<string, CapacitySweep>;
}

export interface PerformanceSummary {
  measured_on: string;
  dataset_label: string;
  hardware: string;
  graph_scope: string;
  execution_mode: string;
  model_inference: ModelPerformance;
  api: Record<string, SerialLatencyStats | InvestigationPerformance>;
  graph_queries: GraphQueryPerformance;
  cost: CostStatus;
  run_id?: string | null;
  git_commit?: string | null;
  concurrency?: ConcurrencyPerformance | null;
}

export interface BenchmarksResponse {
  models: Record<string, ModelBenchmark>;
  performance?: PerformanceSummary | null;
}

export interface InvestigationRequest {
  transaction_id: string;
  max_hops: number;
  time_window_seconds: number;
  result_limit: number;
  use_llm: boolean;
  act: boolean;
}

export interface TransactionEvidence {
  transaction_id: string;
  timestamp: string;
  source_account_key: string;
  destination_account_key: string;
  amount_paid?: number | null;
  payment_currency?: string | null;
  amount_received?: number | null;
  receiving_currency?: string | null;
  payment_format?: string | null;
}

export interface AccountEvidence {
  account_key: string;
}

export interface GraphPath {
  anchor_transaction_id: string;
  direction: string;
  depth: number;
  account_keys: string[];
  transaction_ids: string[];
}

export type ToolName =
  | "trace_funds"
  | "inspect_counterparties"
  | "detect_flow_patterns.fan_out"
  | "detect_flow_patterns.fan_in"
  | "detect_flow_patterns.pass_through";

export interface ToolDuration {
  tool: ToolName;
  query_duration_ms: number;
}

export interface InvestigationEvidence {
  transactions: TransactionEvidence[];
  accounts: AccountEvidence[];
  paths: GraphPath[];
  query_duration_ms: number;
  tool_durations_ms: ToolDuration[];
}

export type InvestigationOutcome =
  | "evidence_found"
  | "no_matching_path"
  | "unknown_anchor"
  | "inconclusive";

export type ToolRunOutcome =
  | InvestigationOutcome
  | "invalid_input";

export type ToolTransport = "direct" | "mcp";
export type ToolOrigin = "policy" | "model";

export interface ToolTraceEntry {
  tool_run_id: string;
  tool: "trace_funds" | "inspect_counterparties" | "detect_flow_patterns";
  transport: ToolTransport;
  origin: ToolOrigin;
  arguments: Record<string, string | number | boolean>;
  outcome: ToolRunOutcome;
  status: "ok" | "error";
  duration_ms: number;
  result_count: number;
  path_count: number;
  truncated: boolean;
}

export type ProposedAction = "none" | "open_case" | "enrich_case";

export type ActionDenialCode =
  | "anchor_not_scored"
  | "outcome_not_actionable"
  | "decision_not_review"
  | "evidence_refs_missing"
  | "evidence_ref_unverified"
  | "case_already_active"
  | "no_open_case"
  | "duplicate_enrichment";

export interface CaseActionResult {
  proposed_action: ProposedAction;
  proposed_by: "agent" | "policy";
  reason: string;
  status: "executed" | "denied" | "no_action";
  denial_code?: ActionDenialCode | null;
  case_id?: string | null;
  evidence_refs: string[];
  tool_run_ids: string[];
}

export type LlmFallbackReason =
  | "not_configured"
  | "provider_error"
  | "timeout"
  | "invalid_output"
  | "verification_failed";

export interface LlmRunInfo {
  provider: string;
  model: string;
  status: "completed" | "fallback" | "unavailable";
  fallback_reason?: LlmFallbackReason | null;
  rounds: number;
  tool_calls: number;
  input_tokens?: number | null;
  output_tokens?: number | null;
  duration_ms: number;
  estimated_cost_usd?: number | null;
}

export interface CitedClaim {
  transaction_id: string;
  timestamp?: string | null;
  amount_paid?: number | null;
  payment_currency?: string | null;
  source_account_key?: string | null;
  destination_account_key?: string | null;
}

export interface CitedPath {
  transaction_ids: string[];
  account_keys: string[];
}

export interface InvestigationReport {
  hypothesis: string;
  supporting_transaction_ids: string[];
  counterevidence_ids: string[];
  recommendation: "review";
  validation: "validated";
  source: "deterministic" | "llm";
  cited_claims: CitedClaim[];
  cited_paths: CitedPath[];
  llm?: LlmRunInfo | null;
  proposed_action: ProposedAction;
  action_reason?: string | null;
}

export interface InvestigationResponse {
  investigation_id: string;
  transaction_id: string;
  risk_score: number;
  decision: PolicyDecision;
  policy_version: string;
  evidence: InvestigationEvidence;
  report?: InvestigationReport | null;
  created_at: string;
  outcome: InvestigationOutcome;
  tool_trace: ToolTraceEntry[];
  action?: CaseActionResult | null;
}

export type CaseStatus = "suspected" | "under_review" | "confirmed" | "dismissed";
export type ReviewAction = "confirm" | "dismiss";
export type HistoryAction = "open" | "enrich" | "confirm" | "dismiss";

export interface CaseHistoryEntry {
  review_id: string;
  action: HistoryAction;
  reviewer_id: string;
  pattern_type?: string | null;
  notes?: string | null;
  timestamp: string;
}

export interface CaseEvidenceEntry {
  evidence_id: string;
  review_id: string;
  action: "open_case" | "enrich_case";
  proposed_by: "agent" | "policy";
  investigation_id: string;
  reason: string;
  evidence_refs: string[];
  tool_run_ids: string[];
  tool_trace: ToolTraceEntry[];
  risk_score: number;
  decision: PolicyDecision;
  policy_version: string;
  timestamp: string;
}

export interface CaseItem {
  case_id: string;
  transaction_id: string;
  status: CaseStatus;
  risk_score: number;
  opened_by: string;
  created_at: string;
  updated_at: string;
  history: CaseHistoryEntry[];
  evidence: CaseEvidenceEntry[];
}

export interface CaseListResponse {
  items: CaseItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface CaseCreateRequest {
  transaction_id: string;
  opened_by: string;
  notes?: string | null;
}

export interface CaseReviewRequest {
  action: ReviewAction;
  reviewer_id: string;
  pattern_type?: string | null;
  notes?: string | null;
}
