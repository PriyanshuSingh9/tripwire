# Kanishka: Frontend and Product UI Charter

## Role

Own the TraceAML analyst workbench and its presentation of alerts, investigations, evidence, and benchmark results.

## Primary paths

- `apps/web/`
- UI components and styles within the web application

## Responsibilities

- Build the Next.js App Router interface with strict TypeScript and Tailwind CSS.
- Own navigation and user flows for Alerts, Investigations, Benchmarks, and System.
- Render graph paths with Cytoscape.js from API-provided node and edge records.
- Present evidence, counterevidence, query scope, validation status, and errors clearly.
- Show benchmark metrics only when backed by recorded experiment artifacts.
- Label any development fixtures as fixtures; never portray them as live graph evidence or actual scores.

## Interfaces and boundaries

- Consume API shapes defined in `docs/contracts.md`; coordinate changes with Parth.
- Do not create graph topology, risk scores, or evidence claims on the client.
- Work independently of dataset ingestion using clearly labelled fixtures when necessary.
- Coordinate graph labels and evidence paths with Ashwani and user evaluation views with the evaluation lead.

## Completion evidence

The UI can display an actual investigation response and benchmark artifact, identifies unavailable services, and makes synthetic replay status clear.

## Current implementation status

- **Implemented:** alert queue, investigation timeline and evidence graph, benchmark view, system page, typed API client, and loading/error/empty states.
- **Implemented:** same-origin `/api` proxy through Next.js using `TRACEAML_API_ORIGIN`, so the browser does not require CORS setup.
- **Implemented:** benchmark view reads the recorded model, endpoint, and graph-query timings from the benchmark API. The global workbench header and footer identify the synthetic AMLworld replay; the home page no longer describes the completed API integration as a scaffold.
- **Implemented:** the System route reads `/api/health` and displays API and FalkorDB status from the response.
- **Verification:** `pnpm lint` and `pnpm build` pass. The investigation route and API proxy returned HTTP 200 against the local services and held-out case. The System route now reads the health contract, and the benchmark response includes the recorded performance snapshot.
- **Pending:** visual review at desktop and mobile sizes, and analyst review of the live evidence and benchmark presentation.
