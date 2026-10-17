# Parth: Backend and Investigation Integration Charter

## Role

Own the FastAPI service, shared API implementation, deterministic decision policy, and integration of scoring, graph tools, reports, and audit history.

## Primary paths

- `services/api/`
- Local service integration in `compose.yaml` and root scripts, coordinated with the project lead

## Responsibilities

- Implement validated Pydantic request/response models and endpoints described in `docs/contracts.md`.
- Coordinate deterministic replay and connect scoring, graph retrieval, and analyst-facing responses.
- Keep policy decisions deterministic and label any `block` outcome as simulated.
- Expose approved bounded investigation tools and pass their returned evidence to the optional report model.
- Validate report transaction IDs, paths, amounts, and timestamps before presenting claims as evidence.
- Persist decisions and explicit analyst review provenance; preserve dismissal history.
- Provide safe behavior when FalkorDB or the optional LLM is unavailable.

## Interfaces and boundaries

- Coordinate graph properties and query payloads with Ashwani.
- Coordinate scoring and metric semantics with the evaluation lead.
- Publish stable contracts for Kanishka; do not require the UI to infer missing data.
- The LLM may summarize retrieved evidence but cannot assign final risk probability or policy action.

## Completion evidence

API responses validate against shared contracts, service failures return typed errors or deterministic fallback, and persisted case changes include reviewer and evidence provenance.

## Current implementation status

- **Complete:** strict Pydantic contracts and `GET /api/health`, `GET /api/alerts`, `GET /api/benchmarks`, and `POST /api/investigations` routes.
- **Complete:** target-free score artifact loading with cached risk-ranked results, deterministic threshold policy, and label-field rejection.
- **Complete:** investigation orchestration calls bounded fund tracing, counterparty inspection, and flow-pattern tools; the verifier rejects transaction IDs and paths absent from returned evidence.
- **Complete:** benchmark response merges `lgbm_graph` metrics when the configured graph-ablation artifact exists, without replacing canonical baseline entries.
- **Complete:** benchmark response includes the validated measured-performance snapshot from `TRACEAML_PERFORMANCE_PATH` when configured.
- **Verified:** live API investigation against the 998,021-transaction target-free demo graph returned validated evidence for a held-out case.
- **Complete (Iteration 2):** one application entrypoint, `api.app:app`, shared by `make dev-api`, the README, and the container image. The parallel `api.main` rewrite was removed: it served hardcoded alerts, seeded fake cases, read graph properties that do not exist, and broke the published response contract.
- **Complete (Iteration 2):** case persistence and review history (`/api/cases`): cases open only from scored transaction IDs, `confirm`/`dismiss` record reviewer provenance, history is append-only at the storage layer, and dismissal never erases records. The SQLAlchemy models and review audit trail build on the cases module from commit `3fa06fd`.
- **Complete (Iteration 2):** investigation tools run concurrently over a pooled, reused FalkorDB connection set; `query_duration_ms` reports evidence-phase wall time, and `tool_durations_ms` reports per-tool time. Domain errors map to typed 404/409/503/504 responses through centralized handlers.
- **Complete (Iteration 2):** optional Gemini report agent (`api.llm`, ADR-020): opt-in via `use_llm`, whitelisted bounded tools, deterministic verification of every cited ID, claim, and path, and deterministic fallback with coarse reason codes. Unit-tested without network; live Gemini verification pending an API key.
- **Pending:** live Gemini verification, graph-side case/ring links on confirmation, and visual analyst review of the frontend integration.
- **Verification:** API Ruff checks pass; `uv run --directory services/api pytest -q` passes, including a live FalkorDB investigation test on a disposable `agentapi-test` graph (skipped when FalkorDB is unreachable). The container image builds from the repository root with `docker build -f services/api/Dockerfile .`.
- **Review handoff:** integration implementation touched `services/api/`; review it against this ownership charter before extending backend behavior.
