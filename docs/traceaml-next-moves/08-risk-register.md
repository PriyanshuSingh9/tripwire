# 08 — Risk Register

Review this list at every integration checkpoint. Severity describes risk to the hackathon submission, not production financial risk.

| ID | Risk | Likelihood | Impact | Mitigation | Release evidence |
|---|---|---|---|---|---|
| R1 | Cross-currency ratio corrupts a high-scoring candidate | Confirmed issue in report | High | Guard/remove ratio unless currencies are compatible; regression tests; rerun candidate | Tests + new feature/config hash + new metrics |
| R2 | Repeated test evaluation leaks test knowledge into model selection | Confirmed runner gap | High | Enforce predeclared manifest; one-way immutable test evaluation; preserve ledger and artifacts | Tests that arbitrary/repeated test runs fail |
| R3 | Earlier baseline AP and latest reevaluation disagree | Open discrepancy | High | Reconcile score/model/evaluator hashes; mark stale result superseded if unexplained | Benchmark note showing exact cause/disposition |
| R4 | Test tail artificially inflates headline score | Reported distribution concern | High | Show full test and September 9–10 bulk; daily prevalence; avoid claims based on one aggregate | Updated report and UI screenshot |
| R5 | MCP exists only as a claim or unused sidecar | Possible | High for Track 1 alignment | Exercise real agent→MCP client/server→existing query function path; show tool trace | Integration test + demo trace |
| R6 | Agent report invents or misstates evidence | Mitigated but not eliminated | High | Strict structured schema, source ID/path validation, deterministic fallback, curated tests | Positive/unsupported-claim tests; visible verifier status |
| R7 | “No path” is interpreted as “safe” | Product semantics risk | High | Typed outcomes; separate model policy; explicit UI language | Negative/inconclusive demo and tests |
| R8 | Case action becomes an unsafe policy override | Low by design if boundary holds | High | LLM proposes only; deterministic policy validates; no score/threshold mutation; local simulated action only | API tests and audit log |
| R9 | Demo graph differs from full graph performance | Existing measurement distinction | Medium/high | Label graph scope in every metric; use demo graph for interaction, full graph for separate performance report | Updated benchmark dashboard |
| R10 | Capacity tests share host with unrelated workload | Some measurements already contaminated | Medium | Quiet host, isolated process, repeated clean windows; label contaminated runs | Benchmark report with contamination flags |
| R11 | Live Gemini unavailable or too slow | Unverified live-provider path | Medium | Deterministic default; short deadlines; tested fallback; run smoke test if possible | Screen recording or logged smoke test/not-run note |
| R12 | UI lacks case actions and provider status | Known gap | Medium/high for demo | Finish case UI and clear LLM mode/status, or narrow demo to supported flows and state limitation | End-to-end walkthrough |
| R13 | Local setup fails for judges | Known Makefile concern | High | Fresh-checkout setup; fix copy command; seed data/service; use clean install | Runbook and clean setup rehearsal |
| R14 | Project claims real-world AML effectiveness | Communication risk | High | Label synthetic replay; explain patterns are not proof; no bank/payment capability claims | README, video and UI copy check |
| R15 | Data license or secret accidentally committed | Avoidable | High | Check CDLA-Sharing-1.0 data terms, use `.gitignore`, scan repository for secrets/large files | `git status`, repository scan, README attribution |
| R16 | Too much late scope prevents submission | Likely under pressure | High | P0/P1 only; no new model family; define release freeze | Team board shows deferred items untouched |
| R17 | Project docs conflict with actual optional agent behavior | Known inconsistency | Medium | Make PRD/ADR/release wording agree: deterministic default, optional Gemini agent | Docs review checklist |
| R18 | Graph schema makes algorithm semantics unclear | Medium | Medium | Define path length in edges/hops given Account→Transaction→Account representation; test exact path | Schema and query tests |

## Stop-ship conditions

Do not present the final build as ready if any of the following hold:

- The selected model still contains the known cross-currency ratio bug.
- The report/UI uses a headline result from a candidate that was not evaluated under the final documented protocol.
- The agent can claim a path/amount that the graph evidence does not contain.
- A no-path or timed-out query is represented as evidence that the transaction is safe.
- README claims working MCP but the main agent path does not invoke the MCP server.
- The demo requires an unconfigured live LLM provider and has no deterministic fallback.
- Public repository/setup/video/track declaration/AI-assistant disclosure are missing.

## Accepted residual risks to disclose

- Synthetic AMLworld data may contain generator regularities that do not generalize to real financial systems.
- Registry fields may represent a static snapshot, not facts available at a precise transaction time.
- High-degree accounts and deeper paths have higher graph-query tail latency.
- Cost figures are estimates when they are derived from list prices and local measurements rather than invoices.
- The report is a prototype for human investigation, not a regulated AML decision engine.
