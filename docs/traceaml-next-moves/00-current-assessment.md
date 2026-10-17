# 00 — Current Assessment and Decisions

**As of:** 2026-10-16  
**Repository revision described:** `84b658b`  
**Status source:** `docs/implementation-report.md` supplied by the team

## 1. Executive assessment

TraceAML has not materially drifted from the original idea. It has progressed from the planned offline baseline and graph prototype into a substantially implemented local-first product:

- Offline data preparation and chronological evaluation are implemented.
- The original tabular baseline and expanded Iteration 2 feature families are implemented.
- FalkorDB ingestion, indices, bounded investigation queries and graph benchmarks are implemented.
- FastAPI exposes alerts, benchmarks, investigations and case endpoints.
- Deterministic reports and evidence verification are implemented.
- An optional Gemini report agent is implemented, but live-provider smoke testing remains open.
- The Next.js workbench connects to the API and renders graph paths and benchmarks.
- Persistent case/review data is implemented in SQLite, but the full review workflow is not yet connected in the UI.

The principal remaining work is not a redesign. It is benchmark correctness and governance, making Track 1's agent/tool/action behavior more visible, finalizing the user journey, running full verification, and producing the submission package.

### Estimated completion (judgment, not measured project accounting)

| Workstream | Assessment | Reasoning |
|---|---|---|
| Data pipeline and split | Near complete | Schema handling, source hashes, chronological split, point-in-time feature rules and manifests exist. |
| Classical/feature experiments | Implemented, needs final validity pass | Multiple feature families and experiment runner exist, but one currency-inconsistent feature and weak test-evaluation guardrails are explicitly reported. |
| FalkorDB | Substantially complete | Full graph load was measured; bounded queries, optimizations and graph tests exist. |
| API and case lifecycle | Substantially complete | Typed routes and SQLite history exist. |
| Agent | Implemented, not fully productized | Optional Gemini path and verifier exist; live provider smoke test and UI control are open. |
| Workbench | Mostly integrated | Alerts, investigations, benchmarks and status routes exist; case management and agent controls are missing from the UI. |
| Release/demo | Incomplete | Full suite, clean deployment walkthrough, live LLM test, final visual review and video remain open. |

Overall percentages are intentionally not treated as exact. Completion is less important than the outstanding tasks' criticality: an untrusted benchmark or broken final demo can outweigh a high percentage of implemented code.

## 1.1 Event-window provenance

The repository's root commit is timestamped 2026-10-15 00:13 IST, twelve minutes after the official 00:01 IST start. Keep the public commit history intact and be ready to explain what was prepared beforehand. The timestamp supports the build timeline but does not, by itself, prove when every design or code change was made. Source: official [hackathon rules](https://www.wemakedevs.org/hackathons/falkordb/rules).

## 2. Original product promise to preserve

TraceAML replays synthetic IBM AMLworld HI-Small transactions. It scores held-out transactions with offline-trained models, retrieves bounded transaction-flow evidence from FalkorDB, and presents a verified investigation report. Reports are deterministic by default, with an optional Gemini agent to draft reports from approved graph evidence.

It is a local-first prototype. It does not connect to a financial institution, block real payments, or file regulatory reports. Its conclusions describe behavior in a synthetic dataset.

Keep that boundary clear in code, product copy, demo voice-over and the README.

## 3. Decisions already made that should remain

1. **Parquet is the analytical source of truth.** Use it for offline feature generation and model evaluation.
2. **FalkorDB is the topology and evidence source of truth.** Live investigation paths must come from executed graph queries.
3. **The target label does not enter online inference.** Labels are joined only by the offline evaluator. Never add laundering labels or typology truth to the online graph.
4. **Transactions at the same minute are simultaneous.** Point-in-time features use activity strictly before the current minute.
5. **Models score; deterministic code decides policy.** The LLM must not set risk scores or override `allow`/`review`.
6. **The agent remains optional.** Deterministic investigation is the reliable default; Gemini is an opt-in report-drafting enhancement.
7. **No custom GNN/LLM training before submission.** The system already has sufficient technical substance. Stabilize, integrate and demonstrate it.
8. **FalkorDB stays central.** The graph cannot be a decorative diagram or an optional lookup that could be replaced without changing the product behavior.

## 4. Critical unresolved issues from the current implementation report

### P0 — Cross-currency feature correctness

`ml/src/ml/feature_families.py` computes `log_received_over_paid` even when paid and received currencies differ. This conflicts with the project's currency semantics. Fix by either (a) making the ratio null/not applicable for cross-currency transactions, or (b) defining it against a documented, dated FX conversion source. For this deadline, option (a) is safer and avoids introducing new dependencies. Add tests for same-currency and mixed-currency examples.

### P0 — Test evaluation is not adequately locked down

`ml/src/ml/experiment.py` reportedly accepts arbitrary candidates and repeated test evaluation can append test-ledger rows and overwrite candidate outputs. Enforce the predeclared manifest for test runs, make the test evaluation one-way, and refuse overwrites unless an explicit archival/new-experiment flow is used. Validation experiments can remain iterative.

### P0 — Reconcile benchmark snapshots

The earlier implementation status gave Iteration 1 LightGBM AP as `0.1853`; the later report lists the saved Iteration 1 score artifact re-evaluated at `0.1587`. The later C1 full-test AP is `0.7525`, while AP on the September 9–10 bulk is `0.5996`. These may reflect different runs, scores, configurations or cohorts, but the difference must be explained. Do not simply choose the largest number.

### P0 — Verify the build from a clean environment

The report identifies a Makefile issue: `.env` copying uses `uv run --no-project python` before the environment has been set up. Replace this operation with a shell copy (`cp`, with platform-appropriate instructions) or run it from an already provisioned environment. Run `make check` after installing the declared environments and starting services required by integration tests.

### P1 — Make graph-driven action explicit

The current API can open and review cases but the UI does not expose those actions. Connect graph evidence to a controlled case workflow: the agent proposes an action; deterministic policy validates the case ID, evidence references and action eligibility; the case repository records it; the UI shows the outcome. This is a simulated investigation action, not payment blocking.

### P1 — MCP is a visible track focus but not yet part of the described path

Track 1 explicitly calls out tool selection through MCP. The current report describes a provider-specific tool adapter. Add a thin MCP interface over the existing fixed graph tools if it can be completed and tested without destabilizing the rest of the agent. Do not rewrite the Cypher queries or adopt a new agent framework.

### P1 — UI, agent and release gaps

- Add an optional-agent control and provider/fallback status.
- Add case creation, review and history controls.
- Add trace visibility: tool selected, bounded arguments, tool duration, result count, returned paths and evidence validation outcome.
- Run a live Gemini smoke test if credentials and provider availability permit; otherwise disclose that live provider behavior is not verified and keep deterministic mode as the demo fallback.
- Finish analyst/visual review and record the demo.

## 5. Recommended priority order

Run two lanes in parallel from the start:

**Track 1 product lane:** within the first integration block, prove the MCP-to-FalkorDB path and a verified case action; then expose the tool/evidence/action trace in the UI and rehearse a positive and a no-path/inconclusive case.

**Correctness/release lane:** fix the currency bug and test guardrails; reconcile the score snapshots; run `make check` and required live FalkorDB tests; then repeat only the measurements needed for the final claims.

Before submission, finish the clean setup walkthrough, video, AI-tool disclosure, track declaration and submission form. Do not let optional provider polish, extra algorithms or performance sweeps delay this release gate.

## 6. Scope boundaries

Do not add the following unless all P0/P1 work above is complete:

- A new GNN or graph transformer.
- Fine-tuning, RLVR/GRPO, or training a Jev-like model.
- Multi-agent orchestration.
- Redis, RabbitMQ, Kafka, vector database, Kubernetes or cloud microservices.
- An unrestricted natural-language-to-Cypher interface.
- Real payment actions or claims that the system proves crime.

## 7. Sources

- Implementation report supplied by the team: `docs/implementation-report.md`, revision `84b658b`, 2026-10-16.
- Official event overview: <https://www.wemakedevs.org/hackathons/falkordb>
- Official rules: <https://www.wemakedevs.org/hackathons/falkordb/rules>
- WeMakeDevs graph-agent guide: <https://www.wemakedevs.org/blogs/how-to-build-an-agent-that-reasons-over-a-graph>
