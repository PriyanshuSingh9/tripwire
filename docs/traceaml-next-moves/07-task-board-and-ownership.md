# 07 — Task Board and Team Ownership

This is a ready-to-copy task board. Adjust ownership in the repository's milestone files if team availability has changed.

## Priority definitions

- **P0:** benchmark/correctness/release blocker.
- **P1:** required to make the Track 1 experience complete and judge-visible.
- **P2:** polish only after all P0/P1 acceptance gates pass.
- **Defer:** not justified in the remaining hackathon window.

P0 and P1 workstreams run concurrently. The first integration checkpoint is the Track 1 path from agent tool selection through FalkorDB evidence to a recorded case action; do not make it wait for final model benchmarking. Keep the benchmark fixes moving in parallel because the current headline model has a known feature-semantics issue.

## Suggested workstream ownership

The implementation report's role-charter map lists Ashwani for graph/data engineering, Kanishka for frontend/product UI, Parth for API/investigation integration, and Priyanshu for fraud intelligence/evaluation/integration lead. Use this mapping if those assignments are still valid; reassign explicitly rather than leaving tasks ownerless.

| Workstream | Suggested owner | Parallel dependency |
|---|---|---|
| Currency feature fix, model finalization, benchmarks | Priyanshu — evaluation | Can start immediately; coordinate feature API with data owner |
| FalkorDB/MCP integration and query behavior | Ashwani — graph/data | Reuse existing query module; expose stable tool contracts |
| Agent bridge, verifier/action API and tests | Parth — API/integration | Needs stable MCP tool and evidence schema |
| Landing walkthrough, case workflow and agent trace | Kanishka — UI with API support | Needs the captured run manifest and final response schema |
| Integration gate, demo script and submission | Priyanshu + all owners | Depends on candidate freeze and working e2e path |

## P0 task list

### P0-01 — Fix currency-inconsistent feature

**Owner:** Evaluation/ML  
**Files:** `ml/src/ml/feature_families.py`, feature tests, feature manifest  
**Done when:** currency mismatches do not produce a raw cross-currency ratio; zero/missing inputs are covered by tests; candidate artifacts identify the corrected feature version.

### P0-02 — Harden final test evaluation

**Owner:** Evaluation/ML  
**Files:** `ml/src/ml/experiment.py`, candidate manifest and ledger tests  
**Done when:** unknown candidates are rejected, repeated final test evaluation cannot silently overwrite artifacts, output hashes align, and reruns require a new experiment identifier.

### P0-03 — Reconcile detection metrics

**Owner:** Evaluation/ML  
**Files:** detection benchmark report and JSON snapshot  
**Done when:** earlier AP `0.1853` vs reevaluated `0.1587` is explained or marked superseded; corrected C1/C2 results have full and bulk-period metrics; final UI/README figures match the report.

### P0-04 — Run full correctness suite

**Owner:** Integration lead plus all workstream owners  
**Files:** test outputs/release note  
**Done when:** `make check` passes or all failures have an explicit, accepted disposition; live FalkorDB tests run; optional provider test is clearly pass/fail/not-run.

### P0-05 — Fix clean setup

**Owner:** API/build owner  
**Files:** root `Makefile`, README  
**Done when:** setup no longer requires `uv run --no-project python` before environment creation; documented commands work from a fresh checkout.

## P1 task list

### P1-01 — Thin MCP server over current query functions

**Owner:** Graph/data + API integration  
**Done when:** the existing `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns` functionality is exposed through typed MCP tools with bounds; a test proves the agent execution path actually uses the MCP server. No duplicate Cypher implementation is created.

### P1-02 — Graph evidence triggers a controlled case action

**Owner:** API/integration  
**Done when:** agent may propose `open_case` or `enrich_case`; deterministic code validates the target-free anchor, evidence refs, graph status and case state; an append-only history entry records the outcome. No risk score or policy override is possible.

### P1-03 — Finish case review UI

**Owner:** Frontend  
**Done when:** reviewer can open a case, see evidence and history, and confirm/dismiss with supported fields; conflicts and API errors are understandable.

### P1-04 — Display agent/tool trace

**Owner:** Frontend + API  
**Done when:** UI displays tool choice, parameters, durations/result counts, evidence IDs, verifier outcome, deterministic/LLM mode, fallback reason and resulting action.

### P1-05 — Add Gemini opt-in and status

**Owner:** Frontend + API  
**Done when:** optional provider call can be explicitly requested; default deterministic path works with no key; failures fall back visibly; no secret/upstream exception is exposed.

### P1-06 — Guided landing walkthrough

**Owner:** Frontend + API/integration
**Done when:** landing page offers a guided walkthrough or direct dashboard entry; the story is sourced from a verified, label-free real-run manifest kept in ignored local artifacts; only its schema and generation instructions are committed; every displayed graph fact matches the captured evidence; “Explore this case” opens the same anchor and persisted/seeded case in the workbench.

### P1-07 — Clean performance pass

**Owner:** Bench/evaluation  
**Done when:** demo graph and full graph results are separate; typical/hub/max-depth anchors are measured; contaminated windows are excluded or labeled; live agent latency/cost is measured only if a live call ran.

### P1-08 — Final demo and submission assets

**Owner:** Integration lead + all  
**Done when:** positive and negative/inconclusive cases are rehearsed, a readable demo video is recorded, public repo/docs work, Track 1 is selected and AI-assistant use is disclosed.

## P2 task list

- One extra FalkorDB algorithm exposed as a tool, if it directly improves the chosen demo and is tested.
- UI micro-polish (keyboard behavior, graph layout, transitions, responsive detail).
- A project blog post for the optional side prize, only if submission gates are already complete.

## Defer

- GNN/TabGT/Multi-GNN model training.
- Fine-tuning a language model, GRPO/RLVR or a Jev-like classifier.
- Multi-agent collaboration.
- Kafka/Redis/queue infrastructure.
- Arbitrary NL-to-Cypher.
- Real-world financial integrations or payment blocking.

## Dependency order

```text
P0-01 currency fix ─┐
P0-02 test guardrail ├─> P0-03 final benchmark ─┐
P0-05 setup fix ─────┘                         ├─> P0-04 full test/clean build
                                               |
Graph tool contract ─> P1-01 MCP ─> P1-02 case action ─> P1-03/P1-04 UI
                                               |
                                               v
                          Early end-to-end demo checkpoint
                                               |
                                               v
                        P1-07 performance ─> P1-08 release/video
```

The UI work may proceed with mock API responses for layout development, but the final demo must use real endpoints and real graph results. Freeze shared response schemas early so parallel work does not produce integration drift.
