"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowUpRight, BellRing, Radar, X } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/badge";
import { SearchInput, SegmentedControl, SelectField } from "@/components/ui/controls";
import { SidePanel } from "@/components/ui/side-panel";
import { DataTable, TablePager, useSortedRows, type Column, type SortState } from "@/components/ui/table";
import { EmptyState, ErrorState, TableSkeleton, InlineLoading } from "@/components/ui/states";
import { KpiCard } from "@/components/ui/kpi-card";
import { DECISION_META, DecisionBadge, OutcomeBadge } from "@/components/ui/semantic";
import { GraphPreview } from "@/components/graph/graph-preview";
import { ALERT_PAGE_MAX } from "@/lib/api/client";
import { useAlerts, useInvestigation, useReviewAlertCount } from "@/lib/api/hooks";
import type { AlertItem, PolicyDecision } from "@/lib/api/types";
import { formatNumber, formatRelative, formatScore, formatTimestamp } from "@/lib/format";
import { useInvestigationLog } from "@/lib/runs/log";

type DecisionFilter = "all" | PolicyDecision;

const PAGE_SIZES = [
  { value: "50", label: "50 rows" },
  { value: "100", label: "100 rows" },
  { value: "200", label: "200 rows (maximum)" },
];

const DEFAULT_SORT: SortState = { columnId: "score", direction: "desc" };

/**
 * Ranked alert queue over the target-free score artifact.
 *
 * Search, sorting and the review counts describe the loaded page, not the whole
 * artifact, and the page says so next to the figures that depend on it.
 */
export function AlertsPage(): React.JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlTransactionId = searchParams.get("transaction_id") ?? "";
  const urlDecision = searchParams.get("decision");

  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  // The URL owns the decision filter so deep links from the overview KPI and
  // the browser history both land on the same queue view.
  const decisionFilter: DecisionFilter =
    urlDecision === "review" || urlDecision === "allow" ? urlDecision : "all";
  const [selectedRow, setSelectedRow] = useState<AlertItem | null>(null);
  const [urlSelectionDismissed, setUrlSelectionDismissed] = useState(false);
  const log = useInvestigationLog();

  const setDecisionInUrl = useCallback(
    (next: DecisionFilter) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === "all") params.delete("decision");
      else params.set("decision", next);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const alerts = useAlerts(limit, offset);
  const reviewCount = useReviewAlertCount();
  const preview = useInvestigation();

  // An on-demand graph read for the selected alert. It is a real bounded query,
  // not a cached figure, so it is issued only when the analyst asks for it. The
  // full workspace run is the one that lands in the local run log.
  const readGraph = useCallback(
    (transactionId: string) => {
      preview.mutate({
        transaction_id: transactionId,
        max_hops: 1,
        time_window_seconds: 86_400,
        result_limit: 25,
        use_llm: false,
        act: false,
      });
    },
    [preview],
  );

  const previewReset = preview.reset;

  const items = useMemo(() => alerts.data?.items ?? [], [alerts.data]);

  // A deep link from another page opens that alert's detail when it is on the
  // loaded page. The selection is derived from the URL rather than synced into
  // state by an effect.
  const linkedAlert = useMemo(() => {
    if (urlSelectionDismissed || urlTransactionId.length === 0) return null;
    return items.find((item) => item.transaction_id === urlTransactionId) ?? null;
  }, [items, urlSelectionDismissed, urlTransactionId]);
  const selected = selectedRow ?? linkedAlert;

  const openRow = useCallback(
    (row: AlertItem) => {
      previewReset();
      setSelectedRow(row);
      const params = new URLSearchParams(searchParams.toString());
      params.set("transaction_id", row.transaction_id);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, previewReset, router, searchParams],
  );

  const closePanel = useCallback(() => {
    previewReset();
    setSelectedRow(null);
    setUrlSelectionDismissed(true);
    const params = new URLSearchParams(searchParams.toString());
    if (params.has("transaction_id")) {
      params.delete("transaction_id");
      const next = params.toString();
      router.replace(next.length > 0 ? `${pathname}?${next}` : pathname, { scroll: false });
    }
  }, [pathname, previewReset, router, searchParams]);

  const logByTransaction = useMemo(() => {
    const map = new Map<string, (typeof log)[number]>();
    for (const entry of log) {
      if (!map.has(entry.transaction_id)) map.set(entry.transaction_id, entry);
    }
    return map;
  }, [log]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter((item) => {
      if (decisionFilter !== "all" && item.decision !== decisionFilter) return false;
      if (needle.length === 0) return true;
      return (
        item.transaction_id.toLowerCase().includes(needle) ||
        item.timestamp.toLowerCase().includes(needle)
      );
    });
  }, [items, search, decisionFilter]);

  const columns = useMemo<Column<AlertItem>[]>(
    () => [
      {
        id: "transaction",
        header: "Transaction",
        sortable: true,
        sortValue: (row) => row.transaction_id,
        cell: (row) => (
          <span className="font-mono text-[11.5px] text-foreground">{row.transaction_id}</span>
        ),
      },
      {
        id: "score",
        header: "Risk score",
        headerTitle:
          "Model estimate from the replay score artifact. A high score is a reason to look, not a finding.",
        align: "right",
        sortable: true,
        sortValue: (row) => row.score,
        cell: (row) => (
          <span className="tabular text-[12.5px] font-medium text-foreground">
            {formatScore(row.score)}
          </span>
        ),
      },
      {
        id: "decision",
        header: "Decision",
        headerTitle: "Deterministic policy decision from the locked score threshold.",
        sortable: true,
        sortValue: (row) => row.decision,
        cell: (row) => <DecisionBadge decision={row.decision} />,
      },
      {
        id: "time",
        header: "Time (UTC)",
        align: "right",
        sortable: true,
        sortValue: (row) => row.timestamp,
        cell: (row) => (
          <span className="tabular text-[11.5px] whitespace-nowrap text-muted-foreground">
            {formatTimestamp(row.timestamp)}
          </span>
        ),
      },
      {
        id: "local",
        header: "Your run",
        headerTitle:
          "Whether this browser has investigated the transaction. The service stores no run history.",
        sortable: true,
        sortValue: (row) => (logByTransaction.has(row.transaction_id) ? 0 : 1),
        cell: (row) => {
          const entry = logByTransaction.get(row.transaction_id);
          if (!entry) return <span className="text-[11.5px] text-muted-foreground">not run here</span>;
          if (entry.status === "error") {
            return (
              <span className="text-[11.5px] text-critical">
                failed {formatRelative(entry.attempted_at)}
              </span>
            );
          }
          return (
            <span className="flex items-center gap-1.5">
              <OutcomeBadge outcome={entry.outcome ?? "inconclusive"} />
              <span className="text-[11px] text-muted-foreground">
                {formatRelative(entry.attempted_at)}
              </span>
            </span>
          );
        },
      },
      {
        id: "action",
        header: "Action",
        align: "right",
        cell: (row) => (
          <span className="flex items-center justify-end gap-1.5">
            <Link
              href={`/investigation?transaction_id=${encodeURIComponent(row.transaction_id)}`}
              className="inline-flex items-center gap-1 rounded-control border border-border-strong bg-secondary px-2 py-0.5 text-[11px] font-medium text-foreground transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover"
            >
              Review
              <ArrowUpRight className="size-3" aria-hidden="true" />
            </Link>
          </span>
        ),
      },
    ],
    [logByTransaction],
  );

  const { rows: sortedRows, sort, toggle } = useSortedRows(filtered, columns, DEFAULT_SORT);

  const error = alerts.error;
  const total = alerts.data?.total ?? 0;
  const loadedCount = items.length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <KpiCard
          label="Alerts awaiting review"
          value={
            reviewCount.data
              ? `${formatNumber(reviewCount.data.count)}${reviewCount.data.exact ? "" : "+"}`
              : null
          }
          state={reviewCount.isPending ? "loading" : reviewCount.isError ? "error" : "ready"}
          stateLabel={reviewCount.isError ? "Queue unavailable" : "No alert is waiting"}
          supporting={
            reviewCount.isError
              ? "The ranked queue could not be read."
              : reviewCount.data?.exact
                ? "Review decisions across the ranked queue, counted from the score artifact."
                : "At least this many: the loaded queue window is all review decisions."
          }
          href="/alerts?decision=review"
          hrefLabel="Show only these"
          icon={BellRing}
          emphasis={reviewCount.data !== undefined && reviewCount.data.count > 0}
        />
        <KpiCard
          label="Scored transactions"
          value={alerts.data ? formatNumber(total) : null}
          state={alerts.isPending ? "loading" : alerts.isError ? "error" : "ready"}
          stateLabel={alerts.isError ? "Artifact unavailable" : "None"}
          supporting="Every row in the replay score artifact, ranked server-side."
          href="/reports?tab=reports"
          hrefLabel="How scores are produced"
        />
      </div>

      <Card className="overflow-hidden">
        <CardHeader
          title="Alert queue"
          description={
            alerts.data
              ? `Ranked by score, then by transaction id. ${formatNumber(total)} scored transactions in the artifact; review decisions sit at the top of the ranking.`
              : alerts.isError
                ? "The ranked queue could not be read. The error below carries the service's own detail."
                : "Reading the ranked queue from the score artifact."
          }
          level={2}
          action={
            <Chip title="The service caps a page at 200 rows">
              page limit {ALERT_PAGE_MAX}
            </Chip>
          }
        />

        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
          <SegmentedControl<DecisionFilter>
            label="Filter by decision"
            size="sm"
            value={decisionFilter}
            onChange={(next) => {
              setOffset(0);
              setDecisionInUrl(next);
            }}
            options={[
              { value: "all", label: "All" },
              { value: "review", label: "Review", hint: DECISION_META.review.meaning },
              { value: "allow", label: "Allow", hint: DECISION_META.allow.meaning },
            ]}
          />
          <SearchInput
            label="Search loaded alerts"
            value={search}
            onChange={setSearch}
            placeholder="Search transaction id or date"
            className="w-[248px]"
          />
          <SelectField
            label="Rows per page"
            hideLabel
            value={String(limit)}
            onChange={(next) => {
              setLimit(Number(next));
              setOffset(0);
            }}
            options={PAGE_SIZES}
          />
          {search.trim().length > 0 || decisionFilter !== "all" ? (
            <Button
              size="sm"
              variant="ghost"
              icon={X}
              onClick={() => {
                setSearch("");
                setOffset(0);
                setDecisionInUrl("all");
              }}
            >
              Clear filters
            </Button>
          ) : null}
          <p className="ml-auto text-[11px] text-muted-foreground">
            {formatNumber(sortedRows.length)} of {formatNumber(loadedCount)} loaded rows after
            filters
          </p>
        </div>

        {alerts.isPending ? (
          <TableSkeleton rows={8} columns={6} />
        ) : error ? (
          <ErrorState
            error={error}
            onRetry={() => void alerts.refetch()}
            hint="The service could not read the score artifact it was configured with. Reports & System shows which dependency is failing."
          />
        ) : items.length === 0 ? (
          <EmptyState
            title="The score artifact has no rows"
            description="The artifact loaded, but it contains no scored transactions, so the queue is empty."
          />
        ) : sortedRows.length === 0 ? (
          <EmptyState
            title="No alert matches the current filters"
            description="Clear the search or switch the decision filter to see the loaded rows."
          />
        ) : (
          <>
            <DataTable<AlertItem>
              columns={columns}
              rows={sortedRows}
              rowKey={(row) => row.transaction_id}
              sort={sort ?? DEFAULT_SORT}
              onSortToggle={toggle}
              caption="Ranked alert queue over the target-free score artifact"
              emptyLabel="No alerts"
              isRowActive={(row) => selected?.transaction_id === row.transaction_id}
              onRowActivate={openRow}
            />
            <TablePager
              total={total}
              limit={limit}
              offset={offset}
              onChange={setOffset}
              label="scored transactions"
            />
          </>
        )}
        {alerts.isFetching && !alerts.isPending ? (
          <InlineLoading label="Refreshing the queue" />
        ) : null}
      </Card>

      <SidePanel
        open={selected !== null}
        onClose={closePanel}
        title="Alert detail"
        subtitle={
          selected ? <span className="font-mono text-[11px]">{selected.transaction_id}</span> : null
        }
        footer={
          selected ? (
            <Link
              href={`/investigation?transaction_id=${encodeURIComponent(selected.transaction_id)}`}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-control bg-primary px-3 py-2 text-[13px] font-medium text-primary-foreground transition-colors duration-[var(--motion-fast)] hover:bg-primary/90"
            >
              Review this alert
              <ArrowUpRight className="size-3.5" aria-hidden="true" />
            </Link>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <DecisionBadge decision={selected.decision} />
              <Chip mono>{formatScore(selected.score)} risk score</Chip>
            </div>
            <dl className="space-y-1">
              <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5">
                <dt className="text-[11px] text-muted-foreground">Transaction</dt>
                <dd className="font-mono text-[11.5px] break-all text-foreground">
                  {selected.transaction_id}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5">
                <dt className="text-[11px] text-muted-foreground">Timestamp (UTC)</dt>
                <dd className="tabular text-[11.5px] text-foreground">
                  {formatTimestamp(selected.timestamp)}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5">
                <dt className="text-[11px] text-muted-foreground">Risk score (model estimate)</dt>
                <dd className="tabular text-[11.5px] text-foreground">
                  {selected.score.toFixed(6)}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1.5">
                <dt className="text-[11px] text-muted-foreground">Your run in this browser</dt>
                <dd className="text-[11.5px] text-foreground">
                  {logByTransaction.has(selected.transaction_id)
                    ? formatRelative(logByTransaction.get(selected.transaction_id)?.attempted_at ?? null)
                    : "not run in this browser"}
                </dd>
              </div>
            </dl>
            <p className="text-[11.5px] leading-relaxed text-muted-foreground">
              {selected.decision === "review"
                ? "Flagged by the scoring model and set to review by the deterministic policy. The queue stores no written reason, so open the review to see what the returned evidence supports."
                : `${DECISION_META.allow.meaning} A low score is not proof of clean activity.`}
            </p>

            <section className="space-y-2 border-t border-border pt-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-[12px] font-medium text-foreground">See connected accounts</h3>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={Radar}
                  loading={preview.isPending}
                  onClick={() => readGraph(selected.transaction_id)}
                >
                  {preview.data ? "Read again" : "Read the graph"}
                </Button>
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                Reads the graph for this transfer at one hop and a 24 hour window, bounded to 25
                records. This is a live read, so it is not saved to your run history.
              </p>
              {preview.isPending ? <InlineLoading label="Reading the graph" /> : null}
              {preview.isError ? (
                <ErrorState
                  error={preview.error}
                  onRetry={() => readGraph(selected.transaction_id)}
                  hint="This preview uses the same graph tools as a full review. If the anchor is missing from the graph, the service says so here."
                />
              ) : null}
              {preview.data ? <GraphPreview response={preview.data} maxNodes={10} /> : null}
            </section>

            <p className="rounded-control border border-border bg-muted px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
              The service stores no run history, so the queue carries no investigation state of its
              own. The activity column and this panel report what this browser actually did.
            </p>
          </div>
        ) : (
          <EmptyState title="No alert selected" />
        )}
      </SidePanel>
    </div>
  );
}
