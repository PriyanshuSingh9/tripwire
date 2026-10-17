# 04 — Graph Tools, MCP, Agent and Action Design

## 1. Design objective

Make Track 1 obvious in the running product: the agent selects a graph tool, FalkorDB returns connected evidence, the application validates the evidence, and a controlled action follows from the result. Do this without rewriting the graph query engine or letting the LLM produce executable Cypher.

Official context:
- Track 1 calls out multi-hop reasoning, graph queries/algorithms as tools, MCP tool selection, actions based on graph results, and explainable outcomes: <https://www.wemakedevs.org/hackathons/falkordb>
- The organizer's graph-agent guide recommends fixed human-written queries selected as tools, validated arguments, visible tool traces, and explicit distinction between no path and unknown entity: <https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph>

## 2. Keep the existing boundaries

Continue to use:

- Parquet for offline rows, feature tables, split metadata and evaluation.
- LightGBM for risk scores.
- FalkorDB for graph topology and live investigation evidence.
- FastAPI for policy, coordination, validation and case operations.
- SQLite for case/review history.
- Gemini only when explicitly enabled for report drafting.

The model cannot set or revise the score. The graph does not contain laundering target labels. A suspected pattern is an investigative signal, not proof of crime. Case opening/enrichment is a simulated local workflow; no payment is blocked.

## 3. Recommended call path

```text
Analyst selects a replay alert
        |
        v
FastAPI resolves target-free score + threshold decision
        |
        v
Investigation runtime provides approved tool schemas to agent
        |
        v
Agent chooses a tool and supplies typed, bounded arguments
        |
        v
MCP client invokes TraceAML MCP server
        |
        v
MCP server validates inputs and calls existing graph query module
        |
        v
FalkorDB executes parameterized Cypher
        |
        v
Structured graph results: path + record IDs + timestamps + amounts + query metadata
        |
        v
Agent may request a bounded follow-up tool; all results are retained
        |
        v
Deterministic evidence verifier validates claims and path membership
        |
        v
Deterministic policy permits/denies case action; SQLite stores audit entry
        |
        v
UI displays graph path, tool trace, report, result status and case history
```

### MCP integration note

The current Gemini adapter uses provider-native function calls, while the project report does not describe an MCP server. To avoid destabilizing the existing agent, implement MCP as a thin tool interface around the same query functions. The runtime may map the provider's selected function call to an MCP client invocation; document that bridge precisely and test it end to end. Do not label the system “MCP-powered” unless the agent's execution path actually calls the MCP server.

If MCP cannot be completed and verified before submission, keep the working agent and describe MCP as an incomplete integration rather than making a false claim. A tested ordinary tool-call flow is better than a nonfunctional MCP checkbox, but MCP alignment is worth prioritizing because it is explicitly highlighted for Track 1.

## 4. Minimum read-only tools

Use the current query module as implementation authority. Names below describe semantic operations; adapt arguments to actual functions.

### `trace_funds`

Purpose: find bounded upstream or downstream transfer paths around the selected transaction/account.

Recommended inputs:
- anchor transaction ID or verified account key;
- direction (`upstream`, `downstream`, or a separately documented bidirectional operation);
- maximum hops constrained to the existing supported range (currently 1–3 according to the report);
- bounded time window;
- result limit within configured maximum.

Outputs:
- outcome status;
- node/edge/path references;
- transaction IDs, source/destination accounts, timestamps and amounts with currencies;
- query duration, truncation/timeout flags and result count.

### `inspect_counterparties`

Purpose: return account/transfer relationships relevant to the selected transaction and a bounded historical window.

Inputs: verified anchor, time window and result limit.

Outputs: counterparties, transaction references, relationship direction, counts/recency where computed, and query metadata.

### `detect_flow_patterns`

Purpose: detect only explicitly implemented patterns such as bounded fan-in, fan-out and pass-through. Do not claim a typology was detected unless the query implements that exact rule.

Inputs: verified anchor, time window, allowed pattern enum and result limit.

Outputs: named pattern/rule ID, supporting transfer records and paths, values with currency, and query metadata.

## 5. Optional algorithm tool

Add at most one graph algorithm if it improves the actual case narrative and the installed FalkorDB version supports it. Candidate categories are shortest path, connected components, community detection or supported centrality. Test with the existing graph schema first; the graph uses first-class Transaction nodes between Account nodes, which affects path length and algorithm interpretation.

For a hackathon demo, it is enough that the algorithm answers one meaningful question and returns explainable node/edge references. Do not add centrality merely as decoration or show unexplained numerical ranks.

## 6. Input validation and budgets

Every MCP tool must have a schema and application-side checks:

- transaction ID exists in the target-free score artifact and/or graph;
- account key resolves to a graph node;
- maximum hop count is an integer in an allowed range;
- time window is bounded and non-negative;
- result limits are capped;
- pattern names come from an enum, not arbitrary text;
- timeout is enforced;
- query strings are fixed and parameterized;
- no model-provided string is concatenated into Cypher.

Set an overall investigation budget: maximum tool calls, maximum rounds, maximum elapsed time, and a result token/byte budget. Use stricter limits for a high-degree account, and return `inconclusive`/truncated status when a limit is reached. A partial result should not be disguised as a complete negative result.

## 7. Typed investigation outcomes

Return one of these statuses consistently across MCP, API and UI:

- `evidence_found`: a relevant path/pattern is returned.
- `no_matching_path`: the graph was queried successfully and the stated pattern/path was not found within the declared bounds.
- `unknown_anchor`: the transaction or account is not in the graph/index; do not proceed to traversal.
- `inconclusive`: timeout, truncation, unavailable graph or incomplete retrieval prevents a reliable conclusion.

Do not map `no_matching_path` to `allow`. The model's policy result remains separate.

## 8. Report schema and deterministic verification

The report should be structured before prose is rendered. Suggested fields:

```json
{
  "anchor_transaction_id": "...",
  "outcome": "evidence_found",
  "observed_patterns": [
    {
      "pattern_id": "bounded_pass_through",
      "claim": "Funds were forwarded through the returned intermediary account within the queried time window.",
      "transaction_ids": ["...", "..."],
      "path_ids": ["..."],
      "confidence_note": "Pattern is an investigative signal, not proof of laundering."
    }
  ],
  "counterevidence": [],
  "limitations": ["Only graph records within the configured time window were examined."],
  "recommended_action": "open_case_for_human_review",
  "evidence_refs": ["..."],
  "tool_trace_ids": ["..."]
}
```

This is an illustrative contract; align names with existing Pydantic schemas. The verifier should check that IDs, paths, dates and amounts correspond exactly to retrieved evidence. Prefer validated structured claim fields over attempting to prove an unrestricted paragraph correct by regex alone. The final prose can be generated from validated fields and source references.

If verification fails, discard the LLM draft and return the deterministic report. Show `llm_fallback` and a safe reason category (e.g. timeout, invalid schema, unsupported evidence), not the provider's raw exception.

Do not surface hidden chain-of-thought or fabricate an internal reasoning trace. Expose the observable tool trace: tool, parameters, results, timing and validation outcomes.

## 9. Controlled action design

An agent demonstrates action if it uses graph evidence to initiate a meaningful, permitted operation. For this project, a local case action is enough.

### Actions

- `open_case`: open a case for a scored transaction if none exists in an incompatible terminal state.
- `enrich_case`: attach evidence references and a structured finding to an existing case.
- `request_human_review`: retain the case in a review-required state.

These actions write to the case subsystem, not to target fields in FalkorDB. Keep the operation idempotent or detect duplicates. Record the score snapshot, evidence IDs, tool trace IDs, policy result, reviewer state and timestamps.

### Deterministic action gate

The LLM may propose an action, but the API executes it only when:
1. anchor transaction is known in the target-free score artifact;
2. required graph query completed (not `unknown_anchor` or unresolved `inconclusive`);
3. every evidence reference exists in the evidence returned by the tool run;
4. the action complies with case-state rules;
5. model score/policy fields are not being overwritten;
6. the action remains within the simulated case-management boundary.

Keep an explicit human review state. The system does not block, freeze or transfer money.

## 10. Make the graph's influence testable

Add integration tests for these cases:

- **Graph path found:** report contains the actual path and the permitted case action references it.
- **No path:** no invented connection and no evidence-based escalation.
- **Unknown anchor:** no traversal occurs.
- **Timeout/truncation:** result is `inconclusive`; no “safe” conclusion is drawn.
- **Unsupported model claim:** verifier rejects the draft and deterministic report is returned.
- **Invalid tool input:** MCP server rejects it before a graph query.
- **Database unavailable:** typed error/fallback behavior is visible.

For the demo, temporarily disabling FalkorDB should remove path investigation functionality. Offline scores may remain visible, but graph evidence and graph-triggered case enrichment should fail closed or be marked unavailable. This demonstrates the graph is central rather than decorative.

## 11. Implementation steps

1. Inspect existing `queries.py`, Pydantic schemas and Gemini tool adapters; write down the true current input/output shapes.
2. Create MCP tool catalogue reusing the three graph query functions.
3. Add a minimal MCP server and a tested client bridge from the agent runtime.
4. Write an integration test that exercises model tool selection → MCP call → FalkorDB query → evidence result.
5. Add case-action proposals and deterministic validation.
6. Expose tool/action trace in the workbench.
7. Run a positive-path and a negative/inconclusive-path demo.

Do not attempt all of this simultaneously. Keep the deterministic investigation endpoint working after each change.
