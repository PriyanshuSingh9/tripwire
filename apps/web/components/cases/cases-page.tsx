"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  ArrowUpRight,
  ClipboardList,
  FolderOpen,
  History,
  Network,
  ShieldCheck,
} from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Chip, StatusBadge } from "@/components/ui/badge";
import { SearchInput, SegmentedControl } from "@/components/ui/controls";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { KpiCard } from "@/components/ui/kpi-card";
import { CASE_STATUS_META, DecisionBadge, ActionStatusBadge } from "@/components/ui/semantic";
import { Disclosure } from "@/components/ui/side-panel";
import { OpenCaseCard } from "@/components/cases/open-case-card";
import { ReviewActions } from "@/components/cases/review-actions";
import { GraphPreview } from "@/components/graph/graph-preview";
import { CASE_PAGE_MAX } from "@/lib/api/client";
import { useCase, useCases } from "@/lib/api/hooks";
import type { CaseItem, CaseStatus } from "@/lib/api/types";
import { formatNumber, formatRelative, formatScore, formatTimestamp } from "@/lib/format";
import { useInvestigationLog } from "@/lib/runs/log";
import { useLoggedInvestigation } from "@/lib/runs/use-investigation-run";

type StatusFilter = "all" | CaseStatus;

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "suspected", label: "Suspected" },
  { value: "all", label: "All" },
  { value: "confirmed", label: "Confirmed" },
  { value: "dismissed", label: "Dismissed" },
];

function statusToParam(filter: StatusFilter): CaseStatus | null {
  return filter === "all" ? null : filter;
}

/**
 * Case review workspace: list, detail and context side by side.
 *
 * The selected case lives in the URL, so a review survives a reload and can be
 * shared. Evidence entries keep their provenance: which run proposed them, what
 * they cited, and which tool runs backed them.
 */
export function CasesPage(): React.JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const urlCaseId = searchParams.get("case_id");
  const urlStatus = searchParams.get("status");

  // Cases awaiting a decision are the default view; the URL owns the filter so
  // the KPI links and the history land on the same list.
  const statusFilter: StatusFilter =
    urlStatus === null
      ? "suspected"
      : STATUS_OPTIONS.some((option) => option.value === urlStatus)
        ? (urlStatus as StatusFilter)
        : "suspected";
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const limit = 50;

  const setStatusInUrl = (next: StatusFilter) => {
    setOffset(0);
    const params = new URLSearchParams(searchParams.toString());
    if (next === "suspected") params.delete("status");
    else params.set("status", next);
    const suffix = params.toString();
    router.replace(suffix.length > 0 ? `${pathname}?${suffix}` : pathname, { scroll: false });
  };

  const list = useCases(statusToParam(statusFilter), limit, offset);
  const total = useCases(null, 1, 0);
  const suspected = useCases("suspected", 1, 0);
  const confirmed = useCases("confirmed", 1, 0);
  const dismissed = useCases("dismissed", 1, 0);

  const selectedCaseId = urlCaseId ?? list.data?.items[0]?.case_id ?? null;
  const detail = useCase(selectedCaseId);

  // Keep the address bar in step with the selected case.
  useEffect(() => {
    if (urlCaseId && urlCaseId === selectedCaseId) return;
    if (!selectedCaseId) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("case_id", selectedCaseId);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }, [selectedCaseId, urlCaseId, pathname, router, searchParams]);

  const items = useMemo(() => list.data?.items ?? [], [list.data]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (needle.length === 0) return items;
    return items.filter(
      (item) =>
        item.case_id.toLowerCase().includes(needle) ||
        item.transaction_id.toLowerCase().includes(needle) ||
        item.opened_by.toLowerCase().includes(needle),
    );
  }, [items, search]);

  const log = useInvestigationLog();
  const lastLocalRun = useMemo(
    () => (detail.data ? log.find((entry) => entry.transaction_id === detail.data.transaction_id) ?? null : null),
    [log, detail.data],
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <KpiCard
          label="Cases awaiting a decision"
          value={suspected.data ? formatNumber(suspected.data.total) : null}
          state={suspected.isPending ? "loading" : suspected.isError ? "error" : "ready"}
          supporting="Suspected cases with no confirm or dismiss recorded."
          href="/cases?status=suspected"
          hrefLabel="Show these"
          icon={FolderOpen}
          emphasis={(suspected.data?.total ?? 0) > 0}
        />
        <KpiCard
          label="Cases on record"
          value={total.data ? formatNumber(total.data.total) : null}
          state={total.isPending ? "loading" : total.isError ? "error" : total.data?.total === 0 ? "empty" : "ready"}
          stateLabel={total.isError ? "Case store unavailable" : "No cases"}
          supporting="Every review record in the local case store."
          href="/cases?status=all"
          hrefLabel="Show all cases"
          icon={ClipboardList}
        />
        <KpiCard
          label="Reviewed"
          value={
            confirmed.data && dismissed.data
              ? formatNumber(confirmed.data.total + dismissed.data.total)
              : null
          }
          state={
            confirmed.isPending || dismissed.isPending
              ? "loading"
              : confirmed.isError || dismissed.isError
                ? "error"
                : "ready"
          }
          stateLabel="Case store unavailable"
          supporting={
            confirmed.data && dismissed.data
              ? `${formatNumber(confirmed.data.total)} confirmed, ${formatNumber(dismissed.data.total)} dismissed.`
              : "Reading the case store."
          }
          icon={ShieldCheck}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,320px)_minmax(0,1fr)_minmax(0,300px)]">
        <Card className="flex flex-col overflow-hidden">
          <CardHeader
            title="Case list"
            description="One active case per transaction; dismissed records stay in history."
            level={2}
          />
          <div className="space-y-2 border-b border-border px-3 py-2.5">
            <SegmentedControl<StatusFilter>
              label="Filter cases by state"
              size="sm"
              value={statusFilter}
              onChange={setStatusInUrl}
              options={STATUS_OPTIONS}
            />
            <SearchInput
              label="Search loaded cases"
              value={search}
              onChange={setSearch}
              placeholder="Search case, transaction, analyst"
            />
          </div>

          {list.isPending ? (
            <TableSkeleton rows={6} columns={2} />
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              title={
                statusFilter === "all"
                  ? "No cases yet"
                  : `No ${statusFilter} case in the store`
              }
              description={
                statusFilter === "all"
                  ? "Cases are created by the controlled action during a review, or directly from the case panel. Nothing is created automatically here."
                  : "Switch the filter to see the other states."
              }
              action={
                <Link
                  href="/alerts"
                  className="inline-flex items-center gap-1.5 rounded-control border border-border-strong bg-secondary px-3 py-1.5 text-[12px] font-medium text-foreground hover:bg-surface-hover"
                >
                  Start from an alert
                  <ArrowUpRight className="size-3" aria-hidden="true" />
                </Link>
              }
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              title="No case matches the search"
              description="Clear the search to see the loaded cases."
            />
          ) : (
            <>
              <ul className="divide-y divide-border">
                {filtered.map((item) => (
                  <li key={item.case_id}>
                    <button
                      type="button"
                      onClick={() => {
                        const params = new URLSearchParams(searchParams.toString());
                        params.set("case_id", item.case_id);
                        router.replace(`${pathname}?${params.toString()}`, { scroll: false });
                      }}
                      aria-current={selectedCaseId === item.case_id ? "true" : undefined}
                      className={
                        selectedCaseId === item.case_id
                          ? "w-full bg-surface-active px-3.5 py-2.5 text-left"
                          : "w-full px-3.5 py-2.5 text-left transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover"
                      }
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-[11px] text-muted-foreground">
                          {item.case_id}
                        </span>
                        <StatusBadge
                          label={CASE_STATUS_META[item.status].label}
                          tone={CASE_STATUS_META[item.status].tone}
                        />
                      </span>
                      <span className="mt-1 block truncate font-mono text-[11.5px] text-foreground">
                        {item.transaction_id}
                      </span>
                      <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                        <span className="tabular">{formatScore(item.risk_score)} risk</span>
                        <span>updated {formatRelative(item.updated_at)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {list.data && list.data.total > limit ? (
                <div className="mt-auto flex items-center justify-between gap-2 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
                  <span className="tabular">
                    {formatNumber(offset + 1)}–{formatNumber(Math.min(offset + limit, list.data.total))} of{" "}
                    {formatNumber(list.data.total)}
                  </span>
                  <span className="flex gap-1.5">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - limit))}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={offset + limit >= list.data.total}
                      onClick={() => setOffset(offset + limit)}
                    >
                      Next
                    </Button>
                  </span>
                </div>
              ) : (
                <p className="mt-auto border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
                  Page limit {CASE_PAGE_MAX}. Filtering happens within the loaded page.
                </p>
              )}
            </>
          )}
        </Card>

        <div className="min-w-0 space-y-3">
          {selectedCaseId === null ? (
            <Card>
              <EmptyState
                title="No case selected"
                description="Choose a case from the list to see its evidence, history and review actions."
              />
            </Card>
          ) : detail.isPending ? (
            <Card>
              <CardHeader title="Loading case record" level={2} />
              <TableSkeleton rows={5} columns={3} />
            </Card>
          ) : detail.isError ? (
            <Card>
              <ErrorState
                error={detail.error}
                onRetry={() => void detail.refetch()}
                hint="A 503 means the local case store is not reachable. Reports & System shows the dependency status."
              />
            </Card>
          ) : detail.data ? (
            <CaseDetail caseItem={detail.data} />
          ) : null}
        </div>

        <div className="min-w-0 space-y-3">
          {detail.data ? (
            <>
              <CaseContextCard caseItem={detail.data} />
              <CaseGraphCard caseItem={detail.data} />
              {lastLocalRun ? (
                <Card>
                  <CardHeader
                    title="Your last run on this anchor"
                    description="Recorded in this browser; the service stores no run history."
                    level={2}
                  />
                  <div className="space-y-1.5 px-4 py-3 text-[11.5px] text-muted-foreground">
                    <p>
                      Last attempt {formatRelative(lastLocalRun.attempted_at)} ·{" "}
                      {lastLocalRun.status === "ok"
                        ? `${lastLocalRun.outcome?.replace(/_/g, " ") ?? "outcome reported"}`
                        : `failed with HTTP ${lastLocalRun.http_status ?? "no response"}`}
                    </p>
                    <Link
                      href={`/investigation?transaction_id=${encodeURIComponent(detail.data.transaction_id)}`}
                      className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
                    >
                      Review the evidence
                      <ArrowUpRight className="size-3" aria-hidden="true" />
                    </Link>
                  </div>
                </Card>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Case header, review actions, evidence entries and history. */
function CaseDetail({ caseItem }: { caseItem: CaseItem }): React.JSX.Element {
  return (
    <>
      <Card>
        <CardHeader
          title={<span className="font-mono text-[12.5px]">{caseItem.case_id}</span>}
          description={`Anchor ${caseItem.transaction_id} · opened by ${caseItem.opened_by}`}
          level={2}
          action={
            <span className="flex flex-wrap items-center gap-2">
              <StatusBadge
                label={CASE_STATUS_META[caseItem.status].label}
                tone={CASE_STATUS_META[caseItem.status].tone}
              />
              <Chip mono>{formatScore(caseItem.risk_score)} risk</Chip>
            </span>
          }
        />
        <div className="grid grid-cols-1 gap-3 px-5 py-3.5 sm:grid-cols-2">
          <dl className="space-y-1">
            <div className="flex items-baseline justify-between gap-3 py-1">
              <dt className="text-[11px] text-muted-foreground">Created</dt>
              <dd className="text-[11.5px] text-foreground">{formatTimestamp(caseItem.created_at)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 py-1">
              <dt className="text-[11px] text-muted-foreground">Updated</dt>
              <dd className="text-[11.5px] text-foreground">{formatTimestamp(caseItem.updated_at)}</dd>
            </div>
          </dl>
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            {CASE_STATUS_META[caseItem.status].meaning}
          </p>
        </div>
        <div className="border-t border-border px-5 py-4">
          <ReviewActions caseItem={caseItem} />
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Evidence attached to this case"
          description="Each entry records the run that proposed the action, what it cited and which tool runs backed it."
          level={2}
          action={<Chip>{caseItem.evidence.length} entries</Chip>}
        />
        {caseItem.evidence.length === 0 ? (
          <EmptyState
            title="No evidence attached"
            description="This case was opened directly rather than by the controlled action gate, so no run evidence is attached. Run an investigation to gather some."
          />
        ) : (
          <ul className="divide-y divide-border">
            {caseItem.evidence.map((entry) => (
              <li key={entry.evidence_id} className="space-y-2 px-5 py-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    label={entry.action === "open_case" ? "Opened case" : "Enriched case"}
                    tone={entry.action === "open_case" ? "primary" : "neutral"}
                  />
                  <DecisionBadge decision={entry.decision} />
                  <Chip mono>{formatScore(entry.risk_score)} risk</Chip>
                  <Chip title="Who proposed the action the gate evaluated">
                    proposed by {entry.proposed_by}
                  </Chip>
                  <span className="text-[11px] text-muted-foreground">
                    {formatTimestamp(entry.timestamp)}
                  </span>
                </div>
                <p className="text-[12px] leading-relaxed text-foreground">{entry.reason}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  {entry.evidence_refs.map((reference) => (
                    <Chip key={reference} mono title="Reference cited by the action">
                      {reference}
                    </Chip>
                  ))}
                </div>
                <Disclosure summary={`Tool runs cited (${entry.tool_run_ids.length})`}>
                  <dl className="space-y-1">
                    <div className="flex items-baseline justify-between gap-3 py-1">
                      <dt className="text-[11px] text-muted-foreground">investigation_id</dt>
                      <dd className="font-mono text-[11px] text-foreground">{entry.investigation_id}</dd>
                    </div>
                    <div className="flex items-baseline justify-between gap-3 py-1">
                      <dt className="text-[11px] text-muted-foreground">policy_version</dt>
                      <dd className="font-mono text-[11px] text-foreground">{entry.policy_version}</dd>
                    </div>
                  </dl>
                  {entry.tool_trace.length === 0 ? (
                    <p className="text-[11px] text-muted-foreground">
                      No tool trace was stored with this entry.
                    </p>
                  ) : (
                    <ul className="space-y-1">
                      {entry.tool_trace.map((run) => (
                        <li
                          key={run.tool_run_id}
                          className="rounded-control border border-border bg-muted px-2.5 py-1.5"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-mono text-[11px] text-foreground">{run.tool}</span>
                            <span className="flex items-center gap-1.5">
                              <ActionStatusBadge
                                status={run.status === "ok" ? "executed" : "denied"}
                              />
                              <Chip mono>{Math.round(run.duration_ms)} ms</Chip>
                            </span>
                          </div>
                          <p className="mt-0.5 font-mono text-[10.5px] text-muted-foreground">
                            {run.tool_run_id} · {run.outcome} · {run.result_count} rows ·{" "}
                            {run.path_count} paths
                            {run.truncated ? " · truncated" : ""}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Disclosure>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Review history"
          description="Every state change with its reviewer and note. Nothing here is overwritten."
          level={2}
          action={<Chip>{caseItem.history.length} entries</Chip>}
        />
        {caseItem.history.length === 0 ? (
          <EmptyState title="No history entries" />
        ) : (
          <ol className="divide-y divide-border">
            {caseItem.history.map((entry) => (
              <li key={entry.review_id} className="flex items-start gap-3 px-5 py-3">
                <History className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] text-foreground">
                    <span className="font-medium">{entry.action}</span> by{" "}
                    <span className="font-mono text-[11.5px]">{entry.reviewer_id}</span>
                  </p>
                  {entry.notes ? (
                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted-foreground">
                      {entry.notes}
                    </p>
                  ) : null}
                  {entry.pattern_type ? (
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      pattern noted: {entry.pattern_type}
                    </p>
                  ) : null}
                </div>
                <span className="shrink-0 text-[11px] whitespace-nowrap text-muted-foreground">
                  {formatTimestamp(entry.timestamp)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </>
  );
}

/** Anchored cross-references: the alert queue and the run workspace. */
function CaseContextCard({ caseItem }: { caseItem: CaseItem }): React.JSX.Element {
  return (
    <Card>
      <CardHeader
        title="Context"
        description="Where this case connects across the app."
        level={2}
      />
      <div className="space-y-2 px-4 py-3.5">
        <Link
          href={`/investigation?transaction_id=${encodeURIComponent(caseItem.transaction_id)}`}
          className="flex items-center justify-between gap-2 rounded-control border border-border bg-muted px-3 py-2 transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
        >
          <span className="text-[12px] text-foreground">Review the evidence</span>
          <ArrowUpRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </Link>
        <Link
          href={`/alerts?transaction_id=${encodeURIComponent(caseItem.transaction_id)}`}
          className="flex items-center justify-between gap-2 rounded-control border border-border bg-muted px-3 py-2 transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
        >
          <span className="text-[12px] text-foreground">Find the alert row</span>
          <ArrowUpRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </Link>
        <Link
          href={`/simulation?transaction_id=${encodeURIComponent(caseItem.transaction_id)}`}
          className="flex items-center justify-between gap-2 rounded-control border border-border bg-muted px-3 py-2 transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
        >
          <span className="text-[12px] text-foreground">Open the advanced workspace</span>
          <ArrowUpRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
        </Link>
        <dl className="pt-1">
          <div className="flex items-baseline justify-between gap-3 py-1">
            <dt className="text-[11px] text-muted-foreground">Anchor transaction</dt>
            <dd className="font-mono text-[11px] break-all text-foreground">
              {caseItem.transaction_id}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3 py-1">
            <dt className="text-[11px] text-muted-foreground">Opened by</dt>
            <dd className="font-mono text-[11px] text-foreground">{caseItem.opened_by}</dd>
          </div>
        </dl>
      </div>
    </Card>
  );
}

/** Graph preview for the case anchor, produced by a real bounded run. */
function CaseGraphCard({ caseItem }: { caseItem: CaseItem }): React.JSX.Element {
  const investigation = useLoggedInvestigation();

  // A new case means a new anchor: drop the previous preview rather than
  // showing another case's graph.
  useEffect(() => {
    investigation.reset();
    return () => investigation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseItem.case_id]);

  return (
    <Card>
      <CardHeader
        title="Connected accounts"
        description="Nothing is drawn until a query returns it, so this runs a bounded investigation on demand."
        level={2}
        action={
          <Button
            size="sm"
            variant="secondary"
            icon={Network}
            loading={investigation.isPending}
            onClick={() =>
              investigation.run({
                transaction_id: caseItem.transaction_id,
                max_hops: 2,
                time_window_seconds: 86400,
                result_limit: 50,
                use_llm: false,
                act: false,
              })
            }
          >
            Read the graph
          </Button>
        }
      />
      <div className="px-4 py-3.5">
        {investigation.error ? (
          <ErrorState
            error={investigation.error}
            onRetry={() =>
              investigation.run({
                transaction_id: caseItem.transaction_id,
                max_hops: 2,
                time_window_seconds: 86400,
                result_limit: 50,
                use_llm: false,
                act: false,
              })
            }
            hint="The preview needs both the score artifact and FalkorDB."
          />
        ) : investigation.result ? (
          <GraphPreview response={investigation.result} maxNodes={12} />
        ) : (
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            No graph loaded for this case yet. Running the query uses the default bounds (2 hops, 24
            hours, 50 records) and does not change the case.
          </p>
        )}
      </div>
    </Card>
  );
}
