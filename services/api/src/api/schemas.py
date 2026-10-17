"""Typed Pydantic request/response contracts for the API service.

Every model excludes target labels and typology truth by construction: no
field exists for them, and Pydantic forbids extra fields, so upstream label
keys are rejected rather than echoed. Amounts always travel paired with
their own currency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model rejecting unknown fields so labels cannot leak through."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    """Health payload per docs/contracts.md. No credentials or labels."""

    status: Literal["ok"]
    falkordb: Literal["available", "unavailable", "not_configured"]
    version: str
    # Whether an LLM provider and key are set. No request is made to the
    # provider to determine this, so the check is free and never spends tokens.
    llm: Literal["not_configured", "configured"] = "not_configured"


class AlertItem(StrictModel):
    """One scored transaction with its deterministic policy decision."""

    transaction_id: str
    timestamp: str
    score: float
    decision: Literal["allow", "review"]


class AlertsResponse(StrictModel):
    """Paged alert queue over the target-free score artifact."""

    items: list[AlertItem]
    total: int
    limit: int
    offset: int


class SplitMetrics(StrictModel):
    """Per-split counts and threshold-selected outcomes."""

    rows: int
    prevalence: float
    alerts: int
    precision: float
    recall: float


class ThresholdMetrics(StrictModel):
    """Test metrics at the validation-selected threshold."""

    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int


class BudgetMetrics(StrictModel):
    """Test metrics at the predeclared fixed review capacity."""

    budget: float
    alerts: int
    true_positives: int
    precision: float
    recall: float


class ModelBenchmark(StrictModel):
    """Benchmark record for one model on the frozen test cohort."""

    average_precision: dict[str, float]
    val_threshold_max_f1: float
    test_at_val_threshold: ThresholdMetrics
    test_at_alert_budget: BudgetMetrics
    splits: dict[str, SplitMetrics]


class LatencyStats(StrictModel):
    samples: int
    p50_ms: float
    p95_ms: float
    mean_ms: float
    p99_ms: float | None = None


class SerialLatencyStats(LatencyStats):
    serial_requests_per_second: float


class CohortInferenceStats(StrictModel):
    rows: int
    repeats: int
    median_seconds: float
    transactions_per_second: float
    threads: int
    max_abs_error_vs_saved_scores: float


class ModelPerformance(StrictModel):
    single_prepared_row: LatencyStats
    full_test_cohort: CohortInferenceStats
    full_test_cohort_all_threads: CohortInferenceStats | None = None
    batches: dict[str, LatencyStats] | None = None


class InvestigationPerformance(SerialLatencyStats):
    graph_query_sum: LatencyStats
    evidence_transactions: int
    evidence_accounts: int
    evidence_paths: int
    all_reports_validated: bool


class FlowQueryPerformance(LatencyStats):
    matches_min: int
    matches_max: int


class GraphQueryPerformance(StrictModel):
    trace_funds: LatencyStats
    inspect_counterparties: LatencyStats
    fan_out: FlowQueryPerformance
    fan_in: FlowQueryPerformance
    pass_through: FlowQueryPerformance


class CostStatus(StrictModel):
    """Cost status; numeric estimates are optional and always labelled estimates."""

    external_llm_calls: int
    external_llm_cost: str
    local_compute_cost: str
    cost_per_1000_decisions: str
    dollar_weighted_recall: str
    price_basis: str | None = None
    scoring_usd_per_1000_transactions: float | None = None
    scoring_cpu_seconds_per_1000_transactions: float | None = None
    investigation_usd: float | None = None
    investigation_cpu_seconds: float | None = None
    investigation_provisioned_usd: float | None = None


class CapacityLevel(StrictModel):
    """One closed-loop concurrency level of a capacity sweep."""

    concurrency: int
    requests: int
    throughput_rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float


class CapacitySweep(StrictModel):
    endpoint: str
    workers: int
    duration_seconds: float
    levels: list[CapacityLevel]
    knee_concurrency: int | None
    peak_throughput_rps: float
    first_error_concurrency: int | None


class ConcurrencyPerformance(StrictModel):
    falkordb_thread_count: int | None = None
    falkordb_max_queued_queries: int | None = None
    sweeps: dict[str, CapacitySweep]


class PerformanceSummary(StrictModel):
    measured_on: str
    dataset_label: str
    hardware: str
    graph_scope: str
    execution_mode: str
    model_inference: ModelPerformance
    api: dict[str, SerialLatencyStats | InvestigationPerformance]
    graph_queries: GraphQueryPerformance
    cost: CostStatus
    run_id: str | None = None
    git_commit: str | None = None
    concurrency: ConcurrencyPerformance | None = None


class BenchmarksResponse(StrictModel):
    """Benchmark metrics read from the offline run artifact."""

    models: dict[str, ModelBenchmark]
    performance: PerformanceSummary | None = None


class InvestigationRequest(StrictModel):
    """Bounded investigation request per the contracts."""

    transaction_id: str = Field(min_length=1)
    max_hops: int = Field(default=2, ge=1, le=3)
    time_window_seconds: int = Field(default=86400, ge=1, le=2592000)
    result_limit: int = Field(default=50, ge=1, le=100)
    # Opt in to the LLM report agent. Off by default so the deterministic
    # path stays fast and free; any LLM failure falls back to that path.
    use_llm: bool = False
    # Opt in to the controlled case action. The proposal (agent or policy) is
    # applied only if the deterministic action gate accepts it.
    act: bool = False


class TransactionEvidence(StrictModel):
    """One transfer record returned by an executed graph query."""

    transaction_id: str
    timestamp: str
    source_account_key: str
    destination_account_key: str
    amount_paid: float | None = None
    payment_currency: str | None = None
    amount_received: float | None = None
    receiving_currency: str | None = None
    payment_format: str | None = None


class AccountEvidence(StrictModel):
    """One account node returned by an executed graph query."""

    account_key: str


class GraphPath(StrictModel):
    """Ordered account and transaction references from query results."""

    anchor_transaction_id: str
    direction: str
    depth: int
    account_keys: list[str]
    transaction_ids: list[str]


ToolName = Literal[
    "trace_funds",
    "inspect_counterparties",
    "detect_flow_patterns.fan_out",
    "detect_flow_patterns.fan_in",
    "detect_flow_patterns.pass_through",
]


class ToolDuration(StrictModel):
    """Duration one approved graph tool measured around its own queries."""

    tool: ToolName
    query_duration_ms: float


class InvestigationEvidence(StrictModel):
    """Evidence arrays containing only records returned by executed queries.

    ``query_duration_ms`` is the wall time of the whole graph evidence phase;
    tools run concurrently. ``tool_durations_ms`` lists each tool's own
    measured duration, so their sum can exceed the wall time.
    """

    transactions: list[TransactionEvidence]
    accounts: list[AccountEvidence]
    paths: list[GraphPath]
    query_duration_ms: float
    tool_durations_ms: list[ToolDuration] = Field(default_factory=list)


InvestigationOutcome = Literal[
    "evidence_found", "no_matching_path", "unknown_anchor", "inconclusive"
]
"""Typed graph result shared by the MCP tools, API and UI.

``no_matching_path`` means the graph was queried successfully and nothing
matched within the declared bounds. It never means the transaction is safe.
"""

ToolRunOutcome = Literal[
    "evidence_found", "no_matching_path", "unknown_anchor", "inconclusive", "invalid_input"
]
ToolTransport = Literal["direct", "mcp"]
ToolOrigin = Literal["policy", "model"]
TraceArgument = str | int | float | bool


class ToolTraceEntry(StrictModel):
    """One observable graph tool call. Never contains model reasoning."""

    tool_run_id: str
    tool: Literal["trace_funds", "inspect_counterparties", "detect_flow_patterns"]
    transport: ToolTransport
    origin: ToolOrigin
    arguments: dict[str, TraceArgument]
    outcome: ToolRunOutcome
    status: Literal["ok", "error"]
    duration_ms: float
    result_count: int
    path_count: int
    truncated: bool


CaseActionName = Literal["open_case", "enrich_case"]
ProposedAction = Literal["none", "open_case", "enrich_case"]
ActionDenialCode = Literal[
    "anchor_not_scored",
    "outcome_not_actionable",
    "decision_not_review",
    "evidence_refs_missing",
    "evidence_ref_unverified",
    "case_already_active",
    "no_open_case",
    "duplicate_enrichment",
]


class CaseActionResult(StrictModel):
    """What the deterministic action gate did with a proposal.

    ``proposed_by`` says who proposed; execution is always decided by code.
    The action writes to the local case store only. It never changes the
    score or the policy decision and never touches a payment.
    """

    proposed_action: ProposedAction
    proposed_by: Literal["agent", "policy"]
    reason: str
    status: Literal["executed", "denied", "no_action"]
    denial_code: ActionDenialCode | None = None
    case_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)


LlmFallbackReason = Literal[
    "not_configured",
    "provider_error",
    "timeout",
    "invalid_output",
    "verification_failed",
]


class LlmRunInfo(StrictModel):
    """What the LLM agent did for one request. Never contains prompts or keys."""

    provider: str
    model: str
    status: Literal["completed", "fallback", "unavailable"]
    fallback_reason: LlmFallbackReason | None = None
    rounds: int = 0
    tool_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: float = 0.0
    # Only set when per-million-token prices are configured; never assumed.
    estimated_cost_usd: float | None = None


class CitedClaim(StrictModel):
    """A factual statement about one retrieved transfer, checked field by field."""

    transaction_id: str
    timestamp: str | None = None
    amount_paid: float | None = None
    payment_currency: str | None = None
    source_account_key: str | None = None
    destination_account_key: str | None = None


class CitedPath(StrictModel):
    """A path the report relies on; it must equal a path returned by a query."""

    transaction_ids: list[str]
    account_keys: list[str]


class InvestigationReport(StrictModel):
    """Report citing only retrieved evidence.

    ``source`` says who wrote the narrative. The recommendation is fixed to
    human review by code, never chosen by the model. ``cited_claims`` and
    ``cited_paths`` are present only for LLM reports and are verified against
    the retrieved evidence before the report is returned.
    """

    hypothesis: str
    supporting_transaction_ids: list[str]
    counterevidence_ids: list[str]
    recommendation: Literal["review"]
    validation: Literal["validated"]
    source: Literal["deterministic", "llm"] = "deterministic"
    cited_claims: list[CitedClaim] = Field(default_factory=list)
    cited_paths: list[CitedPath] = Field(default_factory=list)
    llm: LlmRunInfo | None = None
    # The agent's proposal only; the action gate decides whether it runs.
    proposed_action: ProposedAction = "none"
    action_reason: str | None = None


class InvestigationResponse(StrictModel):
    """Full investigation payload: decision inputs plus graph evidence."""

    investigation_id: str
    transaction_id: str
    risk_score: float
    decision: Literal["allow", "review"]
    policy_version: str
    evidence: InvestigationEvidence
    report: InvestigationReport | None
    created_at: datetime
    outcome: InvestigationOutcome
    tool_trace: list[ToolTraceEntry] = Field(default_factory=list)
    action: CaseActionResult | None = None
