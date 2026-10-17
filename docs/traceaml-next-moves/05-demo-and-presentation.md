# 05 — Demo and Presentation Plan

## 1. Positioning

**One-line pitch:** TraceAML investigates suspicious synthetic transactions by following connected money flows in FalkorDB, lets an agent select bounded graph tools, and turns verified evidence into an auditable case for human review.

**What makes it Track 1:** The graph is not a visualization bolted onto a classifier. The agent uses relationships in FalkorDB to choose what to inspect next, the path determines which evidence can be cited, and validated evidence can drive a controlled case action.

**What it is not:** A bank-integrated product, a payment blocker, proof that a transaction is criminal, or a claim of real-world AML effectiveness.

### Competition calibration

The WeMakeDevs recap of its September First Commit event is a useful product-presentation reference, not a Graph Hacks scoring rubric. It reports 13,667 signups, 4,270 teams and 1,180 submissions, with three main prize winners and four runners-up. That is a 27.6% submission rate per formed team; seven named prize outcomes equal 0.59% of submissions. These are event participation/outcome ratios, not TraceAML's odds: First Commit had a different format and award structure. The recap highlights winners with a clear user, a complete action loop, a deployed or directly usable experience, and—in the Best UI winner's case—an interaction that was easy to understand by watching. It publishes no judging scores or weights. Use that as a reminder to make TraceAML's graph-driven investigation and case action visible; do not infer that Graph Hacks uses the same criteria. Source: <https://www.wemakedevs.org/blogs/first-commit-hackathon-recap-and-whats-next>.

## 2. Audience and problem statement

Address an AML analyst or investigator, not an abstract “AI user.” An isolated transaction can look unremarkable, while its connections to intermediary accounts or rapid fund movement may warrant review. The tool helps the analyst see connected evidence faster and audit the path used to produce the report.

Avoid opening with a long model architecture tour. Start with the analyst problem, then prove that relationships change what the system can find and do.

### Landing page and guided walkthrough

The landing page should offer two clear routes:

- **Walk me through an investigation:** launch a guided visual story based on one completed run of the agent.
- **Open the workbench:** go directly to the interactive dashboard.

The guided story follows the real run in short steps: alert and LightGBM score, selected graph tool and bounded arguments, FalkorDB's returned multi-hop path, evidence verification, and the resulting local review-case action. Highlight only nodes, edges, IDs and values present in that run's response. Include short analyst-facing explanations of what each component did and what the result does not prove.

Capture the story source from an actual completed agent execution after the MCP path and case action work. Generate a small reproducible manifest with the anchor, graph/data and score artifact identifiers, tool calls, returned evidence, verification result and case-action reference. Keep this data-derived manifest under an ignored local artifact path; document its schema and generation command, but do not commit transaction records or evidence. Keep target labels and typology truth out of the manifest and all client-facing story data. Label the experience as a recorded investigation on synthetic data; do not imply each step is a new live provider call.

At the end, **Explore this case** should open the workbench with the same anchor selected and its case available, either from the persisted demo state or a documented seed step. The analyst can inspect the evidence or rerun the investigation. A separate dashboard case with no matching path or an inconclusive result should show that the agent does not invent evidence. If the captured run or provider is unavailable, the story still works from the verified run artifact and the interactive workbench retains its deterministic fallback.

## 3. Recommended 3–4 minute live demo

Choose the exact anchor transaction and expected paths before recording. Store the reproducible case manifest under an ignored local artifact path and document how to regenerate it. It can include the transaction ID and graph/score artifact hashes, but must not include target labels or typology truth. The presenter can explain the outcome using the separate offline evaluation record; the agent and online API must never receive that label.

### 0:00–0:20 — Problem and product

Say: “A transaction score tells us where to look. It does not show the full money flow. TraceAML follows the graph, retrieves the actual transactions, and turns supported findings into a review case.”

Show the alert queue and synthetic-replay label. State that the underlying dataset is synthetic.

### 0:20–0:45 — Choose an alert

Open one test-cohort alert. Show the score, policy outcome (`allow`/`review`), transaction time, amount and currency. Explain that the score was generated offline and the target label is not available in the online inference path.

### 0:45–1:30 — Agent chooses a graph tool

Click “Investigate.” Show the MCP tool choice, validated inputs, query duration and result count. The agent should call a fixed `trace_funds` or equivalent tool. Show the actual FalkorDB path with transaction IDs and amounts. If a second tool is chosen, explain which question it answers that the first query did not.

The demo should make clear that the LLM did not write Cypher. A human-authored, bounded query was invoked through the tool interface.

### 1:30–2:05 — Multi-hop discovery

Show a path with more than one hop and highlight the intermediary account(s). Click one evidence item and show its source transaction details. Explain the limited claim: the path is a pattern that warrants investigation, not proof of laundering.

### 2:05–2:35 — Controlled action

Open or enrich the local case based on the verified evidence. Show the action reason, linked evidence IDs, and human-review status in the case history. Explain that the action does not block money or submit an official regulatory report.

### 2:35–3:00 — Negative or inconclusive case

Open a case where the graph returns no matching path, an unknown anchor, or a bounded/inconclusive result. Show that the system distinguishes these outcomes and does not invent a path. Prefer `no_matching_path` or `inconclusive` over a manufactured “clean” result.

### 3:00–3:40 — Benchmarks and closing

Show model comparison, full-test and bulk-period metrics, recall at fixed alert capacity, graph latency, and API latency. Explain which benchmark was corrected and which run is final. Distinguish demo-graph latency from full-graph measurements and the LLM-disabled path from live Gemini latency.

Close with: “The model ranks what deserves attention; FalkorDB establishes the connected evidence; the agent selects the next bounded investigation; deterministic validation ensures that only supported evidence becomes a case action.”

## 4. UI must make these things visible

### Alert queue
- Transaction ID and source timestamp.
- Amount and currency, not an unlabelled currency-agnostic total.
- Model score, selected threshold decision and model/candidate identity if available.
- Label identifying synthetic replay data.
- Link to investigation.

### Graph explorer
- Distinguish Account nodes from Transaction nodes.
- Label edge direction, hop count, transaction timestamp and amounts with currency.
- Show the actual path returned by the query, not a layout-generated inference.
- Show query depth/window and result truncation where relevant.
- Allow a reviewer to click an edge/transaction to see its source record.

### Investigation timeline
- Ordered trace of tool selected, validated arguments, started/finished time, duration and result count.
- `evidence_found`, `no_matching_path`, `unknown_anchor`, or `inconclusive` result.
- Evidence verification status.
- Deterministic report vs LLM-assisted report status.
- Fallback status and safe fallback reason.
- Recommended action, policy result and case history.

### Benchmark page
- Selected candidate and feature families.
- Full test cohort and date-range metrics.
- September 9–10 bulk-period metrics.
- Positive prevalence and alert count.
- PR-AUC/average precision, precision, recall, F1, fixed-alert-budget recall.
- Graph query P50/P95 and API P50/P95 with workload labels.
- Clear separation between measured costs and price-based estimates.

## 5. Visual design principles

- Use a restrained investigation-workbench design: neutral/dark canvas, one consistent accent for selected paths, red/orange only for warning/risk status, and a separate neutral color for normal graph entities.
- Avoid rainbow graphs. Keep node types visually distinguishable and label only the selected path by default to prevent clutter.
- Use tables next to the graph so evidence is readable even when nodes overlap.
- Ensure every latency number has its workload context. A 12 ms API call on a demo graph must not look like an all-cases production guarantee.
- Provide empty, loading, error, timeout and fallback states.
- Do not animate the graph in a way that obscures source paths or implies causal relationships not returned by Cypher.

## 6. Suggested final architecture slide

```text
IBM AMLworld HI-Small
        |
        v
Temporal feature pipeline ---> LightGBM risk score
        |                              |
        v                              v
FalkorDB topology <---------- Investigated test alert
        |
        v
Fixed graph tools through MCP
        |
        v
Evidence verifier ---> Deterministic case action / optional LLM draft
        |
        v
TraceAML analyst workbench
```

Explain the roles clearly:
- Parquet and the offline evaluator are the analytical source of truth.
- LightGBM ranks transactions.
- FalkorDB stores topology and returns live paths.
- MCP exposes safe tools to the agent.
- The verifier checks claims against tool outputs.
- SQLite stores case/review history.
- The UI renders evidence and performance; it does not fabricate graph facts.

## 7. Video and presentation deliverables

Create one 3–4 minute video with the preceding storyline. Include a brief title card naming Track 1. Record at a resolution where graph labels and evidence IDs are readable. Use a clean seeded demo state and remove unrelated desktop notifications/secrets.

Recommended edit structure:
1. 10-second title and problem.
2. 30-second alert selection.
3. 60-second graph tool selection and multi-hop path.
4. 30-second verified case action.
5. 20-second negative/inconclusive outcome.
6. 30-second benchmark comparison and caveat.
7. 10-second architecture/closing.

Keep a live walkthrough script and local recording as backup in case the provider or hosted deployment is unreliable.

## 8. Claims to avoid

Do not claim:
- The system detects real-world financial crime at the reported test performance.
- A graph cycle or fan-out proves laundering.
- It has measured Gemini cost/latency if no live provider call was measured.
- Full-graph latency equals demo-slice latency.
- The graph-enhanced classifier is “FalkorDB-powered” unless the wording distinguishes offline vectorized graph-feature generation from online FalkorDB investigation.
- A result chosen after repeated test evaluation is an untouched held-out benchmark.
- MCP is integrated unless the actual execution path traverses the MCP interface.

## 9. Backup path

The demo must still be usable without a Gemini API key. The deterministic report is the default. If live Gemini fails, the UI should show the fallback state rather than stall. Seed/verify FalkorDB before the recording and test the exact demo anchor against the clean environment.
