# UX redesign: non-technical analyst experience

This document records the migration from the six-destination console to a four-destination
fraud-review application. It is the route-to-functionality map for the redesign; the
authoritative API contracts remain in `docs/contracts.md`.

## Audience and constraints

The primary user understands transactions and suspicious activity but not machine learning,
graph databases or agent orchestration. Nothing may be invented: every figure comes from an
API response, a saved artifact, or a verified runtime read, and browser-local records are
labeled as browser-local. Model scores stay model estimates, policy decisions stay
deterministic outcomes, and only an analyst confirmation marks a case confirmed.

## New navigation

Four primary destinations, plus one advanced group:

| Destination | Route | Job |
| --- | --- | --- |
| Home | `/` | What needs attention and where to start |
| Fraud alerts | `/alerts` | Find and open what needs review |
| Cases | `/cases` | Track investigations and record decisions |
| Reports & System | `/reports` | Recorded performance (`?tab=reports`) and diagnostics (`?tab=system`) |
| Advanced workspace (advanced group) | `/simulation` | Full pipeline controls, execution rail and workspace |

The guided investigation at `/investigation` opens from an alert or a case and is not a
primary navigation item.

## Route-to-functionality migration map

| Previous route or surface | New home | Notes |
| --- | --- | --- |
| `/` Overview (KPIs, dependency probes, latest run evidence, run log, failures) | Home for the action summary; probes and failures stay in `/reports?tab=system` | Home now shows alerts awaiting review, cases awaiting a decision, the top of the ranked queue, and a compact browser-local activity card |
| `/alerts` Alert queue | `/alerts` Fraud alerts (kept) | Friendlier columns (`Risk score`, `Decision`, `Your run`), URL-owned decision filter, visible clear-filters control, drawer links into `/investigation` |
| `/simulation` run workspace | `/simulation` Advanced workspace (kept as the advanced group entry) | The same form and declared bounds; the result now opens as the agent window: a chat-style replay of the recorded run beside the evidence graph that grows with it, ending in the escalation chain, the evidence table and the case action |
| (none) | `/investigation` Guided investigation (new) | Auto-runs on arrival with default bounds; trigger summary, connections, evidence, next action; advanced options disclosure holds the full form |
| `/cases` | `/cases` (kept) | Default list view is cases awaiting a decision; confirmed and dismissed stay one filter away; cross-links lead with the guided investigation |
| `/benchmarks` | `/reports?tab=reports` (redirect) | Content unchanged |
| `/system` | `/reports?tab=system` (redirect) | Content unchanged |
| `/investigations` | `/investigation` (redirect, query preserved) | Previously redirected to `/simulation`; old deep links keep working |

## Data honesty in the new surfaces

- **Alerts awaiting review** is counted by paging the ranked queue until the first allow
  decision. Ranked order is score-descending and the review cut is a score threshold, so
  every review row sits above every allow row and the count is exact when the boundary is
  reached. If the review band exceeds the page budget (2,000 rows), the figure is shown as
  "at least" instead of guessed. The service exposes no aggregate count endpoint.
- **Activity and failures** are browser-local only. The service keeps no run history, and
  every surface that shows this data says so.
- **Amounts** are not part of the alert queue payload, so the queue does not display them;
  the investigation result and evidence table show the recorded amounts and currencies.
- The guided investigation auto-runs one bounded query set per named transaction and never
  animates a stage as complete before the response arrives. Failures stop the workflow and
  show the service's own detail with a retry.

## Follow-up: overview figures and dimensional surfaces

- Home carries two live figures under the KPI row, both drawn from the same ranked queue
  window that the review count already pages, so no extra request is made:
  - **Risk score distribution**: 0.05 score bins over the loaded window, split by review
    and allow decisions, with the review cut marked from the last review row.
  - **Score by queue rank**: every loaded row plotted as score against rank, with the same
    cut line.
- Both cards state their parameters (window size, bin width, cut rank and score) in the
  legend, caption and tooltips, and carry a plain-language `role="img"` description for
  assistive technology. `GET /api/alerts` remains the only source; nothing is invented
  and no new endpoint or field is used.
- Surface treatment: `surface-raised`, `bevel-accent` and `bevel-neutral` utilities in
  `app/globals.css` give cards, buttons, the segmented control, the active navigation item
  and the side panel a top-lit gradient, a hairline highlight and a soft shadow. The new
  Recharts figures use gradient fills and gradient strokes. Only neutral tones take part,
  so semantic colours keep their meaning.

## Follow-up: the agent window on /simulation

- The advanced result view is split into two sections: the agent activity timeline on the
  left, the investigation graph on the right, with the escalation chain, the selection
  panel, the evidence table, the risk panel and the case action below.
- The service answers one request with one completed response and exposes no streaming
  endpoint, so growth is an explicit replay of the recorded run. The timeline follows
  `tool_trace` in order; the graph draws the records the response attributes to each call.
  Attribution uses only fields the response carries: pattern paths by their recorded
  pattern and account, traversal paths by direction and depth, counterparty records as the
  records that are not on any returned path. Records the attribution cannot pin arrive at
  an evidence step, so the final graph equals the full response and no record is invented.
- The console renders what the service reports: tool name, transport (`direct` or the MCP
  boundary), origin (`policy` or `model`), the recorded arguments in plain words, outcome,
  duration, record and path counts, truncation, and the raw argument JSON behind a
  disclosure. Raw Cypher text and model reasoning are not part of the response, and the
  console states that instead of implying them.
- Workspace canvases now draw labelled entity cards: an account carries its number and
  bank, a transfer its amount and currency. Growth is incremental: new elements fade in
  over 180 ms while the layout animates them into place (260-320 ms). Reduced motion skips
  both and opens the replay at the final state. The card canvases use a force layout: this
  dataset is full of account self-loops, so layered row layouts degenerate into one wide
  line. Flow is the compact solve, Force spreads it out.
- Replay controls: play or pause, step back and forward, restart, skip to end, and a
  caption naming the current recorded step. Selecting a message moves the cursor to that
  step; every control is a real button and every message header is a keyboard-reachable
  jump target.
- The escalation card mirrors the chain-of-escalation reference: screened, policy decision,
  bounded graph investigation (accent), checked report, and the controlled case gate,
  ending in the terminal rating (for example `REVIEW · case change denied` with the gate's
  own denial sentence) and the standing caption that the agent adds scrutiny only.
