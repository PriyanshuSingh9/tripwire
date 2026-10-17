# 02 — Prioritized Build Plan

**Objective:** finish TraceAML without expanding scope.  
**Planning assumption:** approximately 48–50 hours remain from the evening of 2026-10-16 until the 2026-10-18 11:59 PM IST submission deadline. Re-check the official schedule before submission: <https://www.wemakedevs.org/hackathons/falkordb/schedule>.

## 1. Principles for the remaining build

1. Stabilize before adding. The current system is broad enough; new work must close a documented gap.
2. Correctness before headline metrics. A lower, defensible result is better than a high result with an invalid feature or test-governance flaw.
3. Reuse the implementation that already works. Do not rewrite the graph query stack to add MCP; put a thin protocol boundary around it.
4. Make Track 1 behavior visible. Judges must be able to see tool selection, graph traversal, evidence, and an action.
5. Protect the deterministic path. The product must still work if the LLM provider is unavailable.
6. Every new component must have an acceptance test and a demo purpose.
7. Any unfinished feature that does not affect the core story gets deferred.
8. Start the Track 1 vertical slice immediately and build it in parallel with benchmark fixes. Do not wait for the entire model audit before proving that an agent-selected graph tool can retrieve evidence and lead to a review case.

### First integration checkpoint

Within the first integration block, demonstrate one real alert flowing through the existing investigation path: an agent selects a bounded tool through the MCP bridge, FalkorDB returns a multi-hop path, deterministic verification accepts the cited evidence, and a local case action records the evidence references. Put a minimal UI trace around this flow early. Benchmark and setup owners can work in parallel. If the MCP bridge or case write is not working at the checkpoint, reduce its scope and keep the deterministic API path working rather than delaying the demo build.

## 2. P0 — Correctness and benchmark integrity

### Task 2.1: Fix cross-currency amount ratio

**Observed issue:** `log_received_over_paid` is defined for all rows despite currency mismatch. That makes the ratio semantically invalid when paid and received amounts are denominated in different currencies.

**Recommended implementation:** for the current deadline, calculate the ratio only when currencies match and both amounts are valid under the feature definition. For mismatched currencies, set it to null/missing or a documented category; add a separate currency-mismatch feature if useful. Do not add undocumented FX conversion.

**Acceptance tests:**
- Same-currency example produces the expected ratio.
- Cross-currency example does not compare raw amounts.
- Missing or zero amount behavior is explicit and tested.
- Feature manifest documents the rule.
- A rebuilt candidate uses the corrected feature and is separately named.

### Task 2.2: Enforce candidate manifest and test lock

**Observed issue:** `ml/src/ml/experiment.py` accepts arbitrary candidates and may repeat test evaluation, append a ledger record, and overwrite files.

**Recommended implementation:**
- Validation grid may run repeatedly.
- Test evaluation accepts only a candidate name/hash in the committed candidate manifest.
- Store one immutable test result per candidate and experiment revision.
- Refuse overwriting a previous test result by default.
- Provide an explicit new experiment ID for any legitimate rerun; keep old artifacts and report why a rerun was necessary.
- Separate “choose candidate on validation” from “evaluate frozen candidate on test” in the CLI.

**Acceptance tests:** unknown candidate is refused; a repeated evaluation fails closed; modified candidate/config hash is treated as a new experiment, not an overwrite; score artifact and metrics reference the same model hash and split manifest.

### Task 2.3: Reconcile model snapshot differences

Earlier status recorded Iteration 1 LightGBM average precision at 0.1853; current report says its saved score artifact re-evaluates to 0.1587. The current Iteration 2 report shows C1 test AP 0.7525 but September 9–10 bulk AP 0.5996.

Do not “correct” this by choosing the most favorable score. Establish a table showing:
- model and feature-set name;
- code commit/config hash;
- score artifact hash;
- evaluator version;
- exact cohort/split manifest;
- metric implementation and alert-budget semantics;
- whether the score came from a clean, uncontaminated run.

If the older result cannot be explained, mark it superseded/unreconciled and do not use it as a baseline in the final demo.

### Task 2.4: Audit late-tail distribution

Produce a daily test table with rows, positive count, prevalence, average precision, recall and precision where meaningful. Investigate the September 11–18 tail, which the report says contains 1,108 rows and 655 positives and is nearly separable for all candidates.

Keep this test analysis clearly diagnostic. Do not tune the selected model or features on the test cohort. If new candidate decisions are made based on the tail analysis, treat the existing test as consumed and report the limitation rather than pretending it remains unseen.

### Task 2.5: Fix setup command and run tests

Replace the `.env` copy through `uv run --no-project python` before environment creation with a shell file-copy operation or an already-configured environment. Run the repository's `make check` once all environments and services are set up. Also run explicit live FalkorDB integration tests where indicated.

**P0 exit gate:** no known invalid currency feature in the promoted candidate; test evaluation locked; score snapshot reconciled; full test command executed; failures fixed or documented before submission.

## 3. P1 — MCP path and graph-driven action

Start this work in parallel with Section 2. These are the main Track 1 differentiators and must not be left until benchmark cleanup is complete.

### Task 3.1: Thin MCP interface over current graph tools

Do not rewrite `graph/src/graph/queries.py`. Expose the existing query operations via an MCP server/tool catalogue with typed parameters and bounded values. The agent runtime must invoke the server tools—not merely print that MCP is supported.

Minimum tools:
- `trace_funds(transaction_id, direction, max_hops, time_window, limit)`
- `inspect_counterparties(transaction_id, time_window, limit)`
- `detect_flow_patterns(transaction_id, time_window, pattern_types, limit)`

Parameter names must match the real query contract. Do not copy the example signatures blindly. Resolve the anchor against known score/graph IDs before traversing.

If the existing Gemini adapter has provider-native function-call schemas, bridge those calls to the MCP client/tool result path instead of implementing a second set of Cypher queries. Document that architecture precisely. If the bridge cannot be made reliable within the deadline, preserve the working deterministic tool path and explicitly disclose that MCP is not yet integrated; do not make a false claim in the submission.

### Task 3.2: Add controlled case action

The agent proposes `open_case` or `enrich_case` with a reason and evidence reference IDs. Deterministic application code verifies:
- the anchor transaction exists in the target-free score artifact;
- each cited transaction/account ID exists in the retrieved evidence set;
- the action is allowed by the case-state rules;
- the graph result is not unknown or an unresolved timeout;
- no model-generated risk score or policy override is accepted.

The case repository stores evidence refs, tool-run IDs, action reason and timestamp. Case writes belong in the API's case subsystem/SQLite, not as target-label writes in FalkorDB.

This is an explicitly simulated case-management action. The product does not block payments or file SARs.

### Task 3.3: Add an algorithm only if it helps

Possible candidates: shortest path, connected component or a supported centrality/community algorithm. Verify the installed FalkorDB version, graph schema and procedure behavior. Add no more than one if it materially improves the chosen demo case.

Acceptance: algorithm output contains interpretable graph references and is reproducible on a fixed case. If the integration cannot be tested before release, defer it.

## 4. P1 — Finish the workbench

### Task 4.1: Case workflow

Connect existing API endpoints to UI controls:
- Open case from an alert/investigation.
- View case status and ordered review history.
- Confirm or dismiss case, with notes and optional pattern when supported.
- Display duplicate/conflict errors in understandable form.

### Task 4.2: Visible agent trace

On an investigation page display:
- deterministic vs LLM-generated report mode;
- each tool chosen and validated argument summary;
- query duration, number of returned records and timeout/truncation flags;
- returned paths and evidence IDs;
- verifier outcome and fallback reason, where applicable;
- resulting case action and review state.

Do not display hidden chain-of-thought. Show the actual tool trace and evidence, not a fabricated internal-reasoning transcript.

### Task 4.3: Agent opt-in and provider status

The Gemini agent currently defaults off. Keep it opt-in. Provide a control to request an LLM-assisted report, a clear loading/error/fallback state, and a mode badge. Keep the deterministic report available even when no provider key is configured.

### Task 4.4: Make evidence paths easy to understand

Clicking a claim should highlight the relevant path and show the supporting transactions, timestamps, amounts and currencies. Clearly label later-observed records if the view includes information after the scored timestamp. Do not make the graph visualization the sole evidence—provide a legible textual table as well.

### Task 4.5: Add a guided landing-page walkthrough

Give users two entry choices: a guided visual story or the interactive workbench. Build the story from a manifest captured from one actual completed agent run, including the selected tool, bounded arguments, FalkorDB response, verifier result and case-action reference. Keep this data-derived manifest in an ignored local artifact path; commit only its schema and generation instructions, not transaction records or evidence. Each step should explain the product behavior while highlighting only evidence from that run. Label it as a recorded investigation on synthetic data; keep target labels and typology truth out of all client-facing data.

The final “Explore this case” action must open the dashboard with the same anchor selected and its case available. Support this with a persisted demo case or an explicit seed step. The story must not require a live Gemini call on every visit, and the dashboard must still support the deterministic path when the provider is unavailable.

**Acceptance:** the guided flow uses a generated, label-free run manifest; displayed IDs, paths and values match the captured API evidence; the handoff opens the same case in the workbench; direct dashboard entry remains available; transaction-level manifest data is excluded from Git.

## 5. P1 — Clean performance verification

Repeat these separately:
- Full graph load: time, peak RSS/server memory, graph memory and row/edge reconciliation.
- Query performance: typical anchor, seeded demo case, high-degree anchor, max configured depth; report P50/P95 and cap/timeout behavior.
- API performance: serial P50/P95 and a small clean concurrency sweep.
- Model scoring: batch and per-transaction inference; record CPU threads and hardware.
- Agent: deterministic path and optional live Gemini path, separately. Do not call provider estimates “measured costs” unless token usage and applicable rates are available.

Do not put contaminated capacity measurements forward as uncontaminated evidence. If the environment cannot be isolated, label the measurement and don't claim production capacity.

## 6. P2 — Release, docs and presentation

- Synchronize `docs/milestones/overview.md` with the actual Iteration 2 state.
- Resolve conflicting PRD/ADR descriptions of whether agent mode is required or optional. Recommended truth: deterministic report is default; LLM agent is opt-in.
- Update README with exact graph model, Cypher query/algorithm list, setup, expected data inputs, commands, license, synthetic-data caveat, Track 1 selection and AI coding-assistant disclosure.
- Make a fresh setup run using only documented steps.
- Record an end-to-end video and keep a backup local demo path.
- Keep raw data, secrets, model binaries and ignored run arrays out of Git; include versioned summary metrics and instructions to regenerate artifacts.

## 7. Explicitly defer

- GNN / Multi-GNN / TabGT training.
- Fine-tuned LLM, RLVR/GRPO, or Jev-like model.
- Multi-agent architecture.
- Queueing, caching infrastructure or deployment complexity not required to make the demo stable.
- Unrestricted generated Cypher.
- Real payment blocking or regulator filing.

## 8. Final exit gate

The project is ready when a clean setup can reproduce the chosen benchmark, launch FalkorDB and the API, load target-free scores, investigate a real case through bounded graph tools, validate the result, open/enrich a local case, let a reviewer inspect it in the UI, and complete the same flow in both the guided landing walkthrough and recorded demo.
