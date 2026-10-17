"use client";

import Link from "next/link";
import { ArrowUpRight, BellRing, ClipboardList } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Chip } from "@/components/ui/badge";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { KpiCard } from "@/components/ui/kpi-card";
import { CASE_STATUS_META } from "@/components/ui/semantic";
import { QueueFigures } from "@/components/overview/queue-figures";
import { RunLogCard } from "@/components/runs/run-log-card";
import { useAlerts, useCases, useReviewAlertCount } from "@/lib/api/hooks";
import { useInvestigationLog } from "@/lib/runs/log";
import { formatNumber, formatRelative, formatScore, formatTimestamp } from "@/lib/format";

const REVIEW_PAGE = 200;

/**
 * Fraud overview.
 *
 * Answers three questions before anything else: how much work is waiting, what
 * it looks like, and where to start. Figures come from the service: the score
 * artifact for alerts and the case store for cases. Activity comes from this
 * browser only, because the service keeps no run history.
 */
export function OverviewPage(): React.JSX.Element {
  const reviewCount = useReviewAlertCount();
  const topAlerts = useAlerts(REVIEW_PAGE, 0);
  const openCases = useCases("suspected", 5, 0);
  const log = useInvestigationLog();

  const reviewRows = (topAlerts.data?.items ?? []).filter((item) => item.decision === "review");
  const review = reviewCount.data;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <KpiCard
          label="Alerts awaiting review"
          value={review !== undefined ? `${formatNumber(review.count)}${review.exact ? "" : "+"}` : null}
          state={
            reviewCount.isPending ? "loading" : reviewCount.isError ? "unavailable" : "ready"
          }
          stateLabel={reviewCount.isError ? "Score artifact unavailable" : "No alert is waiting"}
          supporting={
            review === undefined
              ? "Counted from the ranked alert queue."
              : review.exact
                ? "Review decisions across the ranked queue, counted from the score artifact."
                : "At least this many: the loaded queue window is all review decisions."
          }
          href="/alerts?decision=review"
          hrefLabel="Review alerts"
          icon={BellRing}
          emphasis={review !== undefined && review.count > 0}
        />
        <KpiCard
          label="Cases awaiting a decision"
          value={openCases.data ? formatNumber(openCases.data.total) : null}
          state={openCases.isPending ? "loading" : openCases.isError ? "unavailable" : "ready"}
          stateLabel={openCases.isError ? "Case store unavailable" : "No case is waiting"}
          supporting="Suspected cases with no confirm or dismiss recorded."
          href="/cases"
          hrefLabel="Open cases"
          icon={ClipboardList}
        />
      </div>

      <QueueFigures
        data={reviewCount.data}
        isPending={reviewCount.isPending}
        error={reviewCount.error}
        onRetry={() => void reviewCount.refetch()}
      />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card className="overflow-hidden">
          <CardHeader
            title="Alerts to review"
            description="Highest scores first. Open one to see the evidence the graph queries return."
            level={2}
            action={<Chip>top {reviewRows.length}</Chip>}
          />
          {topAlerts.isPending ? (
            <TableSkeleton rows={3} columns={2} />
          ) : topAlerts.isError ? (
            <ErrorState error={topAlerts.error} onRetry={() => void topAlerts.refetch()} />
          ) : reviewRows.length === 0 ? (
            <EmptyState
              title="No alert is waiting for review"
              description="The loaded queue contains no review decision. Open the full queue to look further."
            />
          ) : (
            <>
              <ul className="divide-y divide-border">
                {reviewRows.slice(0, 5).map((item) => (
                  <li key={item.transaction_id}>
                    <Link
                      href={`/investigation?transaction_id=${encodeURIComponent(item.transaction_id)}`}
                      className="flex items-center justify-between gap-3 px-5 py-2.5 transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover"
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-[11.5px] text-foreground">
                          {item.transaction_id}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-muted-foreground">
                          risk {formatScore(item.score)} · {formatTimestamp(item.timestamp)}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1 text-[11.5px] font-medium text-primary">
                        Review
                        <ArrowUpRight className="size-3" aria-hidden="true" />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              <div className="border-t border-border px-5 py-2.5">
                <Link
                  href="/alerts?decision=review"
                  className="inline-flex items-center gap-1 text-[11.5px] font-medium text-primary hover:underline"
                >
                  See all alerts awaiting review
                  <ArrowUpRight className="size-3" aria-hidden="true" />
                </Link>
              </div>
            </>
          )}
        </Card>

        <Card className="overflow-hidden">
          <CardHeader
            title="Cases awaiting a decision"
            description="Suspected cases in the local case store, most recently updated first."
            level={2}
            action={<Chip>{openCases.data?.total ?? 0}</Chip>}
          />
          {openCases.isPending ? (
            <TableSkeleton rows={3} columns={2} />
          ) : openCases.isError ? (
            <ErrorState error={openCases.error} onRetry={() => void openCases.refetch()} />
          ) : (openCases.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              title="No case is waiting"
              description="Cases appear here when an investigation opens one, or when you open one yourself."
            />
          ) : (
            <ul className="divide-y divide-border">
              {openCases.data?.items.map((item) => (
                <li key={item.case_id}>
                  <Link
                    href={`/cases?case_id=${encodeURIComponent(item.case_id)}`}
                    className="flex items-center justify-between gap-3 px-5 py-2.5 transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-[11.5px] text-foreground">
                        {item.transaction_id}
                      </span>
                      <span className="mt-0.5 block text-[11px] text-muted-foreground">
                        {CASE_STATUS_META[item.status].label} · risk {formatScore(item.risk_score)} ·
                        opened by {item.opened_by}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-muted-foreground">
                      {formatRelative(item.updated_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <RunLogCard
        entries={log}
        limit={3}
        showClear={false}
        title="Recent activity in this browser"
      />
    </div>
  );
}
