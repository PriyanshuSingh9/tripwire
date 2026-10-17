"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Chip } from "@/components/ui/badge";
import {
  AXIS_STYLE,
  CHART_COLORS,
  ChartCard,
  ChartKey,
  GRID_STYLE,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from "@/components/ui/chart";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import type { ReviewQueueCount } from "@/lib/api/hooks";
import { formatNumber, formatScore } from "@/lib/format";

/** Scores are probabilities in [0, 1]; these bins cover the range in steps of 0.05. */
const BIN_WIDTH = 0.05;
const BIN_COUNT = 20;

const CUT_COLOR = "rgba(230, 231, 229, 0.55)";

interface QueueFiguresProps {
  data: ReviewQueueCount | undefined;
  isPending: boolean;
  error: Error | null;
  onRetry: () => void;
}

/**
 * The two live figures on the overview.
 *
 * Both are drawn from the ranked queue window the review count already pages,
 * so every bin and every point is a real scored row and no extra request is
 * made. The captions state the parameters (window size, bin width, where the
 * review cut falls) so the pictures can be read without guessing.
 */
export function QueueFigures({
  data,
  isPending,
  error,
  onRetry,
}: QueueFiguresProps): React.JSX.Element {
  const bins = useMemo(() => {
    const buckets = Array.from({ length: BIN_COUNT }, (_, index) => {
      const low = index * BIN_WIDTH;
      const high = (index + 1) * BIN_WIDTH;
      return {
        center: low + BIN_WIDTH / 2,
        range: `${Math.round(low * 100)}–${Math.round(high * 100)}%`,
        review: 0,
        allow: 0,
      };
    });
    for (const item of data?.items ?? []) {
      const index = Math.min(BIN_COUNT - 1, Math.max(0, Math.floor(item.score / BIN_WIDTH)));
      const bucket = buckets[index];
      if (bucket) bucket[item.decision] += 1;
    }
    return buckets;
  }, [data]);

  const byRank = useMemo(
    () => (data?.items ?? []).map((item, index) => ({ rank: index + 1, score: item.score })),
    [data],
  );

  const cut = useMemo(() => {
    if (!data || !data.exact || data.count <= 0 || data.count >= data.items.length) return null;
    const lastReview = data.items[data.count - 1];
    const firstAllow = data.items[data.count];
    if (lastReview?.decision !== "review" || firstAllow?.decision !== "allow") return null;
    return { rank: data.count, score: lastReview.score };
  }, [data]);

  const windowRows = data?.items.length ?? 0;
  const firstScore = byRank[0]?.score;
  const lastScore = byRank[byRank.length - 1]?.score;

  const rankTicks = useMemo(() => {
    if (windowRows <= 1) return windowRows === 1 ? [1] : [];
    return Array.from(
      new Set([1, Math.round(windowRows / 3), Math.round((2 * windowRows) / 3), windowRows]),
    );
  }, [windowRows]);

  const histogramAlt =
    windowRows === 0
      ? "Risk score distribution with no loaded alerts."
      : `Risk score distribution over ${formatNumber(windowRows)} loaded alerts, binned at ${BIN_WIDTH} score.${
          cut
            ? ` ${formatNumber(cut.rank)} alerts score at or above the review cut of ${formatScore(cut.score)}.`
            : ""
        }`;

  const rankAlt =
    windowRows === 0
      ? "Risk score by queue rank with no loaded alerts."
      : `Risk score against queue rank for ${formatNumber(windowRows)} loaded alerts, from ${
          firstScore === undefined ? "an unknown score" : formatScore(firstScore)
        } at rank 1 down to ${
          lastScore === undefined ? "an unknown score" : formatScore(lastScore)
        } at rank ${formatNumber(windowRows)}.${
          cut
            ? ` The review cut sits at rank ${formatNumber(cut.rank)}, score ${formatScore(cut.score)}.`
            : ""
        }`;

  const figure = (
    emptyTitle: string,
    emptyDescription: string,
    chart: React.ReactNode,
  ): React.ReactNode => {
    if (isPending) return <TableSkeleton rows={4} columns={2} />;
    if (error) return <ErrorState error={error} onRetry={onRetry} />;
    if (windowRows === 0) return <EmptyState title={emptyTitle} description={emptyDescription} />;
    return chart;
  };

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <ChartCard
        title="Risk score distribution"
        description="Every alert in the loaded queue window, grouped into 0.05 score bins and split by the decision its score maps to."
        action={windowRows > 0 ? <Chip>{`${formatNumber(windowRows)} alerts`}</Chip> : undefined}
        caption={
          windowRows === 0
            ? "Drawn from the ranked queue when the review count loads."
            : `Window: ranks 1 to ${formatNumber(windowRows)} of the queue, loaded live from the score artifact.${
                cut
                  ? ` Review cut at ${formatScore(cut.score)}, rank ${formatNumber(cut.rank)}; the allow decisions below it sit in the lowest bins.`
                  : ""
              }`
        }
        height={248}
      >
        {figure(
          "No scored alert to chart",
          "The loaded queue window is empty, so there is no distribution to draw.",
          <div className="flex h-full flex-col">
            <ChartKey
              items={[
                { label: "Review decision", color: CHART_COLORS.warning },
                { label: "Allow decision", color: CHART_COLORS.muted },
                { label: "Review cut", color: CUT_COLOR },
              ]}
            />
            <div role="img" aria-label={histogramAlt} className="min-h-0 flex-1 pt-1">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={bins} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                  <defs>
                    <linearGradient id="queueHistReview" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={CHART_COLORS.warning} stopOpacity={0.9} />
                      <stop offset="100%" stopColor={CHART_COLORS.warning} stopOpacity={0.32} />
                    </linearGradient>
                    <linearGradient id="queueHistAllow" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={CHART_COLORS.muted} stopOpacity={0.85} />
                      <stop offset="100%" stopColor={CHART_COLORS.muted} stopOpacity={0.3} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...GRID_STYLE} vertical={false} />
                  <XAxis
                    type="number"
                    dataKey="center"
                    domain={[0, 1]}
                    ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
                    tick={AXIS_STYLE}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
                  />
                  <YAxis
                    tick={AXIS_STYLE}
                    axisLine={false}
                    tickLine={false}
                    allowDecimals={false}
                    tickFormatter={(value: number) => formatNumber(value)}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    labelStyle={TOOLTIP_LABEL_STYLE}
                    labelFormatter={(label) => {
                      const bin = bins.find((entry) => entry.center === label);
                      return bin ? `Score ${bin.range}` : "";
                    }}
                    formatter={(value, name) => [formatNumber(Number(value)), String(name)]}
                  />
                  {cut ? (
                    <ReferenceLine
                      x={cut.score}
                      stroke={CUT_COLOR}
                      strokeDasharray="3 3"
                      label={{
                        value: "review cut",
                        position: "insideTop",
                        fill: AXIS_STYLE.fill,
                        fontSize: 10.5,
                      }}
                    />
                  ) : null}
                  <Bar
                    dataKey="allow"
                    name="Allow"
                    stackId="queue"
                    fill="url(#queueHistAllow)"
                    maxBarSize={34}
                    isAnimationActive={false}
                  />
                  <Bar
                    dataKey="review"
                    name="Review"
                    stackId="queue"
                    fill="url(#queueHistReview)"
                    maxBarSize={34}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive={false}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>,
        )}
      </ChartCard>

      <ChartCard
        title="Score by queue rank"
        description="How risk scores fall as the ranked queue is read from the top toward the review cut."
        action={
          windowRows > 0 && cut ? <Chip>{`cut at rank ${formatNumber(cut.rank)}`}</Chip> : undefined
        }
        caption={
          windowRows === 0
            ? "Drawn from the ranked queue when the review count loads."
            : `Each point is one ranked alert, plotted as score against queue rank.${
                cut
                  ? ` The dashed line is the review cut: rank ${formatNumber(cut.rank)} at ${formatScore(cut.score)}.`
                  : ""
              } Loaded live from the score artifact.`
        }
        height={248}
      >
        {figure(
          "No scored alert to chart",
          "The loaded queue window is empty, so there is no decay curve to draw.",
          <div className="flex h-full flex-col">
            <ChartKey
              items={[
                { label: "Risk score", color: CHART_COLORS.primary },
                { label: "Review cut", color: CUT_COLOR },
              ]}
            />
            <div role="img" aria-label={rankAlt} className="min-h-0 flex-1 pt-1">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={byRank} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
                  <defs>
                    <linearGradient id="queueRankFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={CHART_COLORS.primary} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={CHART_COLORS.primary} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...GRID_STYLE} vertical={false} />
                  <XAxis
                    type="number"
                    dataKey="rank"
                    domain={[1, Math.max(windowRows, 2)]}
                    ticks={rankTicks}
                    tick={AXIS_STYLE}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(value: number) => formatNumber(value)}
                  />
                  <YAxis
                    tick={AXIS_STYLE}
                    axisLine={false}
                    tickLine={false}
                    domain={[0, 1]}
                    ticks={[0, 0.25, 0.5, 0.75, 1]}
                    tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    labelStyle={TOOLTIP_LABEL_STYLE}
                    labelFormatter={(label) => `Rank ${formatNumber(Number(label))}`}
                    formatter={(value, name) => [formatScore(Number(value)), String(name)]}
                  />
                  {cut ? (
                    <ReferenceLine
                      x={cut.rank}
                      stroke={CUT_COLOR}
                      strokeDasharray="3 3"
                      label={{
                        value: "review cut",
                        position: "insideTopRight",
                        fill: AXIS_STYLE.fill,
                        fontSize: 10.5,
                      }}
                    />
                  ) : null}
                  <Area
                    type="linear"
                    dataKey="score"
                    name="Risk score"
                    stroke={CHART_COLORS.primary}
                    strokeWidth={1.5}
                    fill="url(#queueRankFill)"
                    dot={false}
                    activeDot={{ r: 2.5, strokeWidth: 0 }}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>,
        )}
      </ChartCard>
    </div>
  );
}
