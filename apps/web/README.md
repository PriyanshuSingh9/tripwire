# TraceAML web workstation

The analyst workbench for TraceAML: a graph-powered AML detection and investigation
console over the synthetic HI-Small v8 replay and FalkorDB.

This app renders only data that the FastAPI service returns, or data this browser
recorded from real responses. It ships no fixtures and no placeholder metrics: when a
dependency is missing, the surface says which dependency is missing and shows the
service's own error detail.

## Stack

- Next.js 16 (App Router, Turbopack) with React 19 and strict TypeScript
- Tailwind CSS 4 with role-based tokens defined once in `app/globals.css`
- TanStack Query 5 for request state
- Cytoscape.js for the evidence graph, Recharts for recorded benchmark charts
- `lucide-react` icons, `clsx` + `tailwind-merge` class composition

Motion is CSS transitions on shared tokens (`--motion-fast`, `--motion-base`,
`--motion-slow`) with a global `prefers-reduced-motion` override, so nothing animates
when the user asks for reduced motion.

## Running it

From the repository root:

```bash
pnpm install
pnpm dev:web        # http://localhost:3000
pnpm build          # production build
pnpm lint
```

The UI calls the same-origin `/api` path. `next.config.ts` rewrites those requests to
`TRACEAML_API_ORIGIN` (default `http://127.0.0.1:8000`), which avoids browser CORS
configuration. Start the API separately:

```bash
uv run --project services/api uvicorn api.app:app --host 127.0.0.1 --port 8000
```

To point the workstation at a different service, set `TRACEAML_API_ORIGIN` in the
environment of the Next.js process (`.env` at the repository root is read by the API,
not by Next.js).

## Routes

| Route | Surface |
| --- | --- |
| `/` | Home (Fraud overview): alerts awaiting review, cases awaiting a decision, top of the ranked queue, compact browser-local activity |
| `/alerts` | Fraud alerts: ranked review queue with search, decision filter and sort, alert drawer, on-demand graph preview |
| `/investigation` | Guided investigation: what triggered the alert, connections, evidence, and the supported next action. Opens from an alert or a case |
| `/cases` | Cases: list, selected-case summary, evidence references, review history, explicit confirm or dismiss |
| `/reports` | Reports & System: `?tab=reports` for recorded benchmarks, `?tab=system` for health, dependency probes, recorded runtime, local session data |
| `/simulation` | Advanced investigation: run form, eight-stage execution rail, Cytoscape views, evidence table, risk panel, case action |
| `/benchmarks` | Redirects to `/reports?tab=reports` |
| `/system` | Redirects to `/reports?tab=system` |
| `/investigations` | Redirects to `/investigation`, preserving the query string |

## Where state lives

- **Service state** (authoritative): every graph record, score, decision and case. The
  UI never caches a response as truth beyond the request it belongs to.
- **Browser-local state**:
  - `traceaml.analyst-id.v1` - the analyst identifier typed into a case action; required
    before confirming or dismissing a case.
  - `traceaml.last-run.v1` (session storage, per tab) - the most recent successful
    investigation response, so the workspace survives a reload.
  - `traceaml.investigation-log.v1` - a local log of investigation attempts this browser
    made, with the real outcome, status and latency. The service keeps no run history,
    so this is the only record of past runs, and it says so on screen.
- **URL state**: `?transaction_id=`, `?case_id=`, `?decision=`, `?status=` and the
  queue paging parameters keep the current selection shareable and back/forward
  navigation honest.

## Data sources behind the screens

- `GET /api/health` - service version, FalkorDB probe, language-model configuration
- `GET /api/alerts` - ranked, target-free replay scores with the policy decision
- `POST /api/investigations` - bounded traversal, evidence, tool trace, deterministic
  report, and the controlled case action when `act` is set
- `GET/POST /api/cases`, `POST /api/cases/{id}/review` - case store and review records
- `GET /api/benchmarks` - the published model metric sets (per-split, alert-budget and
  operating-point figures) and the recorded performance report

## Verification notes

Verified against the real stack, not fixtures: the FastAPI service on
`127.0.0.1:8000`, the FalkorDB replay graph (`traceaml-demo`: 998,021 transactions,
354,660 accounts), the published score artifact (761,639 scored rows) and the live case
store. Every route was exercised in a production build (`next build` then `next start`)
in headless Chromium at 390, 768, 1440 and 1728 px wide with no unexpected console
errors. Empty states and service errors were exercised for real, including an unknown
transaction (404), duplicated case enrichment (`duplicate_enrichment`), an invalid
traversal bound (422) and a missing artifact (503 with the service's own detail string).
Success paths were exercised end to end on real data: a bounded investigation with
evidence graph, alert search with on-demand graph preview, case open and analyst
confirmation (`POST /api/cases/{id}/review`, HTTP 200), benchmark rendering from the
published artifacts, and the dependency probes. The earlier development-only harness is
retired and is not part of this package.

Language-model agent: with a provider configured (`LLM_PROVIDER`, `LLM_MODEL`,
`LLM_API_KEY` in the repository `.env`), the report agent performs real tool-calling
rounds and the report records provider, model, round count and token usage; when a
provider call fails the service falls back to the deterministic report, the failure kind
is recorded on the run and the UI states it. With no provider configured, the agent
control stays disabled and System reports `not configured`. Note that the provider
rejects per-call deadlines below ten seconds, so the client floors its per-request
timeout accordingly (`services/api/src/api/llm/gemini.py`); the provider's own free-tier
quota and demand limits still govern whether a run completes.
