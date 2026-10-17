# 06 — Release and Submission Checklist

**Target:** a judge can clone the public repository, follow the README, start the services, run the demo and understand the graph architecture without help from the team.

The official rules require a public source repository, README with setup and graph model, Cypher queries or graph algorithms used by the product, demo video, live deployment or complete local setup instructions, declared track, and disclosure of AI coding-assistant use. Participants must understand and be able to explain the code and technical decisions. Source: <https://www.wemakedevs.org/hackathons/falkordb/rules>.

## 1. Code and correctness gates

- [ ] Correct or remove `log_received_over_paid` for mismatched currencies.
- [ ] Add regression tests for currency behavior, feature input projection and zero/missing amounts.
- [ ] Enforce candidate-manifest membership for test evaluation.
- [ ] Prevent accidental repeat/overwrite of final test artifacts.
- [ ] Reconcile the Iteration 1 AP report discrepancy (`0.1853` earlier vs `0.1587` in the current reevaluation), or mark the earlier figure unreconciled and superseded.
- [ ] Re-run the selected corrected candidate and regenerate the final score/metrics snapshot.
- [ ] Run the daily/bulk-tail diagnostic report and display full and bulk metrics together.
- [ ] Run all unit tests across ML, graph, API, benchmark and web areas.
- [ ] Run live FalkorDB integration tests with the expected graph schema.
- [ ] Run `make check` after the environment setup fix; record command output or a concise test report.
- [ ] Resolve all critical failing tests. If an optional live-provider test cannot run, document why and retain deterministic mode.

## 2. Local setup gate

Test the README on a fresh checkout or clean environment, not just the developer's already-configured shell.

- [ ] Required versions and prerequisites are documented (Python/uv, Node/pnpm, Docker).
- [ ] `.env.example` contains names and safe defaults only; no secrets.
- [ ] The Makefile `.env` copy operation does not depend on an uninitialized uv project.
- [ ] FalkorDB starts through Compose and health check behaves correctly.
- [ ] Data placement and license/attribution are documented. Raw dataset is not committed.
- [ ] Setup creates the Python environments and JS dependencies.
- [ ] Data preparation commands reproduce the expected profile, split and feature manifests.
- [ ] Graph loader has a documented clean-graph mode and warns when merging into an existing graph.
- [ ] Model artifacts are either downloadable/reproducible or the README clearly documents how to generate them.
- [ ] API and web start commands work; ports, URLs and expected startup sequence are clear.
- [ ] The seeded demo alert exists in the generated score artifact and resolves to a graph anchor.
- [ ] There is a deterministic fallback if the Gemini provider or API key is unavailable.

## 3. Graph and Track 1 gate

- [ ] FalkorDB is named as the primary graph database.
- [ ] README explains why connected data is required for the investigation.
- [ ] Graph model lists node labels, relationship types, important properties, identifiers, and directionality.
- [ ] Cypher queries are documented and the code location for their executable forms is listed.
- [ ] Query bounds, time windows, result limits, timeouts and unknown-anchor behavior are explained.
- [ ] The model does not generate arbitrary executable Cypher.
- [ ] MCP is described accurately: if integrated, document its actual call path; if not, do not claim MCP support.
- [ ] The positive demo proves multi-hop paths and graph tool selection.
- [ ] A negative/inconclusive demo proves that missing paths are not turned into fabricated evidence.
- [ ] Graph results can cause a permitted, deterministic case-management action.
- [ ] The UI displays evidence references and action audit trail.

## 4. Agent and evidence gate

- [ ] Deterministic investigation is the default.
- [ ] Gemini is explicitly optional and user-invoked.
- [ ] Provider status, error and fallback state are displayed in the workbench.
- [ ] LLM cannot change risk scores, threshold policy or decision outcome.
- [ ] Every displayed transaction/account/path citation belongs to retrieved evidence.
- [ ] If schema/claim validation fails, the deterministic report is returned.
- [ ] Timeout, truncated result, unknown anchor and no matching path remain separate outcomes.
- [ ] Tool rounds/calls, depth, rows and elapsed time are bounded.
- [ ] No provider key or raw upstream error appears in logs, API responses, video or README.
- [ ] Run the opt-in live Gemini smoke test if provider credentials and connectivity are available; otherwise record it as not run, not passed.

## 5. Benchmark and claim gate

- [ ] Final model candidate, feature list, code/config hash, model hash and split hash agree.
- [ ] Threshold metrics and fixed-alert-budget metrics are labeled separately.
- [ ] PR-AUC/average precision, precision, recall and F1 are documented with positive prevalence.
- [ ] Test bulk-period and full-test results are both shown.
- [ ] Amount-weighted recall is per currency and has a precise definition; no mixed-currency sum.
- [ ] Model, graph query and end-to-end API measurements are separate.
- [ ] Demo-graph and full-graph measurements are labeled separately.
- [ ] Contaminated host windows are marked and not presented as clean capacity results.
- [ ] Provider latency/cost claims have a real provider measurement and usage/rate inputs.
- [ ] All slides, UI labels, README and video agree with the corrected final metrics.

## 6. UI/UX and case lifecycle gate

- [ ] Landing page offers a guided investigation walkthrough and direct dashboard entry.
- [ ] Walkthrough uses a manifest captured from an actual completed agent run, not invented or hand-authored graph results.
- [ ] The transaction-level walkthrough manifest stays in an ignored local artifact path; only its schema and regeneration instructions are committed.
- [ ] Walkthrough data contains no laundering labels or typology truth and is labeled as a recorded synthetic-data investigation.
- [ ] Every displayed tool call, path, transaction ID, amount and verification state matches the captured evidence.
- [ ] “Explore this case” opens the workbench with the same anchor selected and its case available after documented setup/seed steps.
- [ ] Alerts route shows real API data, not placeholder content.
- [ ] Investigation route shows returned graph paths and a legible evidence table.
- [ ] Case creation and confirm/dismiss/review history work from the UI.
- [ ] Case-action failures and duplicate conflicts have understandable states.
- [ ] Agent opt-in and deterministic fallback are visible.
- [ ] Empty, loading, API unavailable, graph unavailable and tool-timeout states work.
- [ ] “Synthetic replay” is visible in the product.
- [ ] No UI text implies that a score proves criminal intent or that TraceAML blocks real transactions.
- [ ] Final review performed at the actual recording/demo viewport size.

## 7. Submission assets

- [ ] Public repository URL works for users without team access.
- [ ] README gives a fast “what it does / why graph / run it / demo” opening.
- [ ] README includes graph schema and Cypher or graph algorithms used.
- [ ] README includes exact setup and environment variables.
- [ ] Data citation, synthetic-data limitation and CDLA-Sharing-1.0 data license are preserved.
- [ ] Track 1 — Agents That Act on Connected Data is explicitly selected in the submission form.
- [ ] AI coding-assistant use is disclosed as required by the official rules.
- [ ] Demo video is uploaded and the link works.
- [ ] Live deployment link works, or local setup instructions are complete and verified.
- [ ] No raw restricted/personal data, secrets, provider keys, or local DB/model files are committed accidentally.
- [ ] Each teammate can explain the model, graph schema, fixed queries, agent limits, evaluation split and caveats.
- [ ] Team has reviewed ownership and submission form fields.
- [ ] Submission completed before October 18, 2026, 11:59 PM IST.

## 8. Final 10-minute rehearsal

1. Open system status; confirm API and FalkorDB are healthy.
2. Open the chosen demo alert.
3. Run a graph investigation and inspect returned path/evidence.
4. Show tool selection, action record and report verifier status.
5. Open a no-path or inconclusive case.
6. Open case history.
7. Open benchmark dashboard and state the final corrected result with caveats.
8. Verify provider-disabled fallback works.
9. Confirm no secrets or unexpected desktop overlays are visible.
10. Ensure the recorded video and submission links are accessible.
