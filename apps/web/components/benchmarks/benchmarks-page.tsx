"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarChart3, FlaskConical, Gauge, Lightbulb, ShieldCheck } from "lucide-react";

import { Card, CardBody, CardHeader, StatLine } from "@/components/ui/card";
import { Chip } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { KpiCard } from "@/components/ui/kpi-card";
import { Disclosure } from "@/components/ui/side-panel";
import {
  AXIS_STYLE,
  CHART_COLORS,
  ChartCard,
  ChartKey,
  formatChartValue,
  GRID_STYLE,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
} from "@/components/ui/chart";
import { SegmentedControl } from "@/components/ui/controls";
import { useBenchmarks } from "@/lib/api/hooks";
import type { ModelBenchmark, PerformanceSummary } from "@/lib/api/types";
import { formatFraction, formatLatency, formatNumber } from "@/lib/format";

interface ModelRow {
  name: string;
  metrics: ModelBenchmark;
}

interface SplitRow {
  split: string;
  rows: number;
  prevalence: number;
  alerts: number;
  precision: number;
  recall: number;
}

/**
 * Benchmarks.
 *
 * Two families of recorded numbers: detection metrics from the frozen
 * evaluation splits, and runtime measurements captured offline. Both carry
 * their provenance, and anything the artifact does not contain is shown as
 * unavailable rather than estimated.
 */
export function BenchmarksPage(): React.JSX.Element {
  const benchmarks = useBenchmarks();
  const models = useMemo<ModelRow[]>(
    () =>
      Object.entries(benchmarks.data?.models ?? {})
        .map(([name, metrics]) => ({ name, metrics }))
        .sort((left, right) => left.name.localeCompare(right.name)),
    [benchmarks.data],
  );

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const selected = models.find((model) => model.name === selectedName) ?? models[0] ?? null;
  const performance = benchmarks.data?.performance ?? null;

  if (benchmarks.isPending) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((index) => (
            <Card key={index}>
              <TableSkeleton rows={2} columns={1} />
            </Card>
          ))}
        </div>
        <Card>
          <CardHeader title="Reading benchmark artifacts" level={2} />
          <TableSkeleton rows={6} columns={4} />
        </Card>
      </div>
    );
  }

  if (benchmarks.isError) {
    return (
      <Card>
        <ErrorState
          error={benchmarks.error}
          onRetry={() => void benchmarks.refetch()}
          hint="The endpoint needs the baseline metrics, the graph-ablation metrics and the performance report. Adjust TRACEAML_BASELINE_METRICS_PATH, TRACEAML_GRAPH_METRICS_PATH and TRACEAML_PERFORMANCE_PATH, or publish those artifacts."
        />
      </Card>
    );
  }

  if (models.length === 0) {
    return (
      <Card>
        <EmptyState
          title="The benchmark artifact contains no model metrics"
          description="The artifacts loaded, but no model metric set is present, so there is nothing to compare."
        />
      </Card>
    );
  }

  const splitRows: SplitRow[] =
    selected === null
      ? []
      : Object.entries(selected.metrics.splits).map(([split, metrics]) => ({
          split,
          rows: metrics.rows,
          prevalence: metrics.prevalence,
          alerts: metrics.alerts,
          precision: metrics.precision,
          recall: metrics.recall,
        }));

  const apChartData = models.map((model) => ({
    model: model.name,
    ...Object.fromEntries(
      Object.entries(model.metrics.average_precision).map(([split, value]) => [split, value]),
    ),
  }));
  const splitNames = [
    ...new Set(models.flatMap((model) => Object.keys(model.metrics.average_precision))),
  ];

  const budgetChartData = models.map((model) => ({
    model: model.name,
    "precision at budget": model.metrics.test_at_alert_budget.precision,
    "recall at budget": model.metrics.test_at_alert_budget.recall,
  }));

  const splitColumns: Column<SplitRow>[] = [
    {
      id: "split",
      header: "Split",
      cell: (row) => <span className="text-[12px] text-foreground">{row.split}</span>,
    },
    {
      id: "rows",
      header: "Rows",
      align: "right",
      cell: (row) => <span className="tabular text-[12px]">{formatNumber(row.rows)}</span>,
    },
    {
      id: "prevalence",
      header: "Positive rate",
      align: "right",
      cell: (row) => (
        <span className="tabular text-[12px] text-muted-foreground">
          {formatFraction(row.prevalence, 3)}
        </span>
      ),
    },
    {
      id: "alerts",
      header: "Alerts raised",
      align: "right",
      cell: (row) => <span className="tabular text-[12px]">{formatNumber(row.alerts)}</span>,
    },
    {
      id: "precision",
      header: "Precision",
      align: "right",
      cell: (row) => <span className="tabular text-[12px]">{formatFraction(row.precision)}</span>,
    },
    {
      id: "recall",
      header: "Recall",
      align: "right",
      cell: (row) => <span className="tabular text-[12px]">{formatFraction(row.recall)}</span>,
    },
  ];

  return (
    <div className="space-y-4">
      {performance ? <RunProvenance performance={performance} /> : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label={`Average precision (${selected?.name ?? "—"})`}
          value={
            selected
              ? formatFraction(
                  selected.metrics.average_precision[splitNames[splitNames.length - 1] ?? ""] ??
                    selected.metrics.average_precision.test ??
                    selected.metrics.average_precision.validation ??
                    0,
                )
              : null
          }
          state={selected ? "ready" : "empty"}
          supporting="Precision-recall area on the recorded evaluation split."
          icon={Gauge}
        />
        <KpiCard
          label="F1 at validation threshold"
          value={selected ? formatFraction(selected.metrics.test_at_val_threshold.f1) : null}
          state={selected ? "ready" : "empty"}
          supporting={
            selected
              ? `Threshold selected on validation: ${selected.metrics.val_threshold_max_f1.toFixed(6)}`
              : undefined
          }
          icon={ShieldCheck}
        />
        <KpiCard
          label="Recall at alert budget"
          value={selected ? formatFraction(selected.metrics.test_at_alert_budget.recall) : null}
          state={selected ? "ready" : "empty"}
          supporting={
            selected
              ? `Budget ${formatFraction(selected.metrics.test_at_alert_budget.budget, 2)} of the test cohort, ${formatNumber(selected.metrics.test_at_alert_budget.alerts)} alerts.`
              : undefined
          }
          icon={BarChart3}
        />
        <KpiCard
          label="Precision at alert budget"
          value={selected ? formatFraction(selected.metrics.test_at_alert_budget.precision) : null}
          state={selected ? "ready" : "empty"}
          supporting="Share of raised alerts that were positive in the frozen test split."
          icon={FlaskConical}
        />
      </div>

      <Card>
        <CardHeader
          title="Model under inspection"
          description="Every metric below belongs to the selected metric set from the published artifact."
          level={2}
          action={
            <SegmentedControl<string>
              label="Select the model metric set"
              value={selected?.name ?? ""}
              onChange={setSelectedName}
              options={models.map((model) => ({ value: model.name, label: model.name }))}
            />
          }
        />
        {selected ? (
          <div className="grid grid-cols-1 gap-4 px-5 py-4 lg:grid-cols-2">
            <div>
              <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Average precision by split
              </h3>
              <dl className="mt-1.5">
                {Object.entries(selected.metrics.average_precision).map(([split, value]) => (
                  <StatLine key={split} label={split} value={formatFraction(value)} />
                ))}
              </dl>
              <h3 className="mt-4 text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Test at the validation threshold
              </h3>
              <dl className="mt-1.5">
                <StatLine
                  label="Precision"
                  value={formatFraction(selected.metrics.test_at_val_threshold.precision)}
                />
                <StatLine
                  label="Recall"
                  value={formatFraction(selected.metrics.test_at_val_threshold.recall)}
                />
                <StatLine label="F1" value={formatFraction(selected.metrics.test_at_val_threshold.f1)} />
                <StatLine
                  label="True positives"
                  value={formatNumber(selected.metrics.test_at_val_threshold.true_positives)}
                />
                <StatLine
                  label="False positives"
                  value={formatNumber(selected.metrics.test_at_val_threshold.false_positives)}
                />
              </dl>
            </div>
            <div>
              <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Test at the alert budget
              </h3>
              <dl className="mt-1.5">
                <StatLine
                  label="Budget"
                  value={`${formatFraction(selected.metrics.test_at_alert_budget.budget, 3)} of cohort`}
                />
                <StatLine
                  label="Alerts"
                  value={formatNumber(selected.metrics.test_at_alert_budget.alerts)}
                />
                <StatLine
                  label="True positives"
                  value={formatNumber(selected.metrics.test_at_alert_budget.true_positives)}
                />
                <StatLine
                  label="Precision"
                  value={formatFraction(selected.metrics.test_at_alert_budget.precision)}
                />
                <StatLine
                  label="Recall"
                  value={formatFraction(selected.metrics.test_at_alert_budget.recall)}
                />
              </dl>
              <h3 className="mt-4 text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Split composition
              </h3>
              <div className="mt-1.5 overflow-hidden rounded-control border border-border">
                <DataTable<SplitRow>
                  columns={splitColumns}
                  rows={splitRows}
                  rowKey={(row) => row.split}
                  caption={`Split composition for ${selected.name}`}
                />
              </div>
            </div>
          </div>
        ) : null}
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard
          title="Average precision by split"
          description="Higher is better. Splits come from the frozen evaluation protocol."
          caption="Values are read from the benchmark artifact; nothing is recomputed here."
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={apChartData} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid {...GRID_STYLE} vertical={false} />
              <XAxis dataKey="model" tick={AXIS_STYLE} axisLine={false} tickLine={false} />
              <YAxis
                tick={AXIS_STYLE}
                axisLine={false}
                tickLine={false}
                domain={[0, "auto"]}
                tickFormatter={(value: number) => value.toFixed(2)}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                formatter={formatChartValue}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: "#858985" }} />
              {splitNames.map((split, index) => (
                <Bar
                  key={split}
                  dataKey={split}
                  fill={
                    [CHART_COLORS.primary, CHART_COLORS.safe, CHART_COLORS.warning, CHART_COLORS.muted][
                      index % 4
                    ]
                  }
                  radius={[2, 2, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Operating point at the alert budget"
          description="Precision and recall for each metric set at the declared alert budget."
          caption="A fixed alert budget trades recall for precision; read both together."
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={budgetChartData} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid {...GRID_STYLE} vertical={false} />
              <XAxis dataKey="model" tick={AXIS_STYLE} axisLine={false} tickLine={false} />
              <YAxis
                tick={AXIS_STYLE}
                axisLine={false}
                tickLine={false}
                domain={[0, 1]}
                tickFormatter={(value: number) => value.toFixed(2)}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                formatter={formatChartValue}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: "#858985" }} />
              <Bar dataKey="precision at budget" fill={CHART_COLORS.primary} radius={[2, 2, 0, 0]} />
              <Bar dataKey="recall at budget" fill={CHART_COLORS.muted} radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <Card>
        <CardHeader
          title="Model comparison"
          description="One row per metric set in the artifact, at each declared operating point."
          level={2}
        />
        <div className="relative overflow-x-auto">
          <table className="w-full border-collapse text-left text-[12px]">
            <caption className="sr-only">Model comparison across recorded operating points</caption>
            <thead>
              <tr className="border-b border-border text-[11px] tracking-[0.04em] text-muted-foreground uppercase">
                <th scope="col" className="px-3 py-2">Metric set</th>
                {splitNames.map((split) => (
                  <th key={split} scope="col" className="px-3 py-2 text-right">
                    AP {split}
                  </th>
                ))}
                <th scope="col" className="px-3 py-2 text-right">F1 at threshold</th>
                <th scope="col" className="px-3 py-2 text-right">Precision at budget</th>
                <th scope="col" className="px-3 py-2 text-right">Recall at budget</th>
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.name} className="border-b border-border/70 last:border-b-0">
                  <th scope="row" className="px-3 py-2 font-mono text-[11.5px] font-medium text-foreground">
                    {model.name}
                  </th>
                  {splitNames.map((split) => (
                    <td key={split} className="tabular px-3 py-2 text-right text-foreground">
                      {model.metrics.average_precision[split] !== undefined
                        ? formatFraction(model.metrics.average_precision[split] ?? 0)
                        : "—"}
                    </td>
                  ))}
                  <td className="tabular px-3 py-2 text-right text-foreground">
                    {formatFraction(model.metrics.test_at_val_threshold.f1)}
                  </td>
                  <td className="tabular px-3 py-2 text-right text-foreground">
                    {formatFraction(model.metrics.test_at_alert_budget.precision)}
                  </td>
                  <td className="tabular px-3 py-2 text-right text-foreground">
                    {formatFraction(model.metrics.test_at_alert_budget.recall)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-border px-4 py-2.5">
          <ChartKey
            items={splitNames.map((split, index) => ({
              label: `AP ${split}`,
              color: [CHART_COLORS.primary, CHART_COLORS.safe, CHART_COLORS.warning, CHART_COLORS.muted][
                index % 4
              ] ?? CHART_COLORS.muted,
            }))}
          />
        </div>
      </Card>

      {performance ? <PerformanceSection performance={performance} /> : null}

      <Card>
        <CardHeader
          title="How to read these numbers"
          description="Limits that apply to every figure on this page."
          level={2}
          action={
            <Chip title="Measured with the published benchmark run, not in this session">
              recorded offline
            </Chip>
          }
        />
        <CardBody className="space-y-2.5 text-[12px] leading-relaxed text-muted-foreground">
          <p>
            Detection metrics come from a frozen evaluation protocol over the synthetic replay
            dataset. They estimate behaviour on that protocol, not on a production banking feed.
          </p>
          <p>
            The operating threshold was selected on the validation split by maximum F1, so the
            test-at-threshold figures are post-selection and slightly optimistic.
          </p>
          <p>
            Alert-budget figures depend entirely on the declared budget. Changing the budget changes
            precision and recall together; the two charts above are the same metric set viewed at
            two operating points.
          </p>
          <p>
            Runtime figures were measured offline on the hardware named in the provenance strip and
            are not live measurements of this deployment. The service serves them as recorded values.
          </p>
          <p>
            Only metric sets present in the published artifacts appear here. A model absent from the
            artifact cannot be compared on this page, and no value is interpolated to fill a gap.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

/** Provenance strip: when, on what data, with which code. */
function RunProvenance({ performance }: { performance: PerformanceSummary }): React.JSX.Element {
  return (
    <Card>
      <CardHeader
        title="Recorded run"
        description="Provenance of the numbers below. These are measurements captured offline, not live values."
        level={2}
      />
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 px-5 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
        <StatLine label="Measured on" value={performance.measured_on} />
        <StatLine label="Dataset" value={performance.dataset_label} />
        <StatLine label="Graph scope" value={performance.graph_scope} />
        <StatLine label="Execution" value={performance.execution_mode} />
        <StatLine label="Hardware" value={performance.hardware} />
        <StatLine label="Run id" value={performance.run_id ?? "not recorded"} tone="muted" />
        <StatLine
          label="Git commit"
          value={
            <span className="font-mono text-[11px]">{performance.git_commit ?? "not recorded"}</span>
          }
          tone="muted"
        />
      </dl>
    </Card>
  );
}

/** Runtime and cost section, straight from the recorded performance report. */
function PerformanceSection({
  performance,
}: {
  performance: PerformanceSummary;
}): React.JSX.Element {
  const apiEntries = Object.entries(performance.api);
  const graphEntries = Object.entries(performance.graph_queries);
  const sweeps = Object.entries(performance.concurrency?.sweeps ?? {});
  const cost = performance.cost;

  return (
    <Card>
      <CardHeader
        title="Runtime and cost"
        description="Latency and cost recorded with the same run as the detection metrics."
        level={2}
        action={<Chip mono>{performance.measured_on}</Chip>}
      />
      <div className="grid grid-cols-1 gap-4 px-5 py-4 lg:grid-cols-2">
        <section>
          <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
            Service endpoints
          </h3>
          <div className="mt-1.5 overflow-hidden rounded-control border border-border">
            <table className="w-full border-collapse text-left text-[12px]">
              <caption className="sr-only">Recorded service endpoint latency</caption>
              <thead>
                <tr className="border-b border-border text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                  <th scope="col" className="px-2.5 py-1.5">Endpoint</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">p50</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">p95</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">Samples</th>
                </tr>
              </thead>
              <tbody>
                {apiEntries.map(([name, stats]) => (
                  <tr key={name} className="border-b border-border/70 last:border-b-0">
                    <th scope="row" className="px-2.5 py-1.5 font-mono text-[11px] font-medium text-foreground">
                      {name}
                    </th>
                    <td className="tabular px-2.5 py-1.5 text-right">{formatLatency(stats.p50_ms)}</td>
                    <td className="tabular px-2.5 py-1.5 text-right">{formatLatency(stats.p95_ms)}</td>
                    <td className="tabular px-2.5 py-1.5 text-right text-muted-foreground">
                      {formatNumber(stats.samples)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {apiEntries.length === 0 ? (
            <p className="mt-1.5 text-[11.5px] text-muted-foreground">
              The performance report contains no endpoint latency entries.
            </p>
          ) : null}
        </section>

        <section>
          <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
            Graph queries
          </h3>
          <div className="mt-1.5 overflow-hidden rounded-control border border-border">
            <table className="w-full border-collapse text-left text-[12px]">
              <caption className="sr-only">Recorded graph query latency</caption>
              <thead>
                <tr className="border-b border-border text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                  <th scope="col" className="px-2.5 py-1.5">Query</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">p50</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">p95</th>
                  <th scope="col" className="px-2.5 py-1.5 text-right">Samples</th>
                </tr>
              </thead>
              <tbody>
                {graphEntries.map(([name, stats]) => (
                  <tr key={name} className="border-b border-border/70 last:border-b-0">
                    <th scope="row" className="px-2.5 py-1.5 font-mono text-[11px] font-medium text-foreground">
                      {name}
                    </th>
                    <td className="tabular px-2.5 py-1.5 text-right">{formatLatency(stats.p50_ms)}</td>
                    <td className="tabular px-2.5 py-1.5 text-right">{formatLatency(stats.p95_ms)}</td>
                    <td className="tabular px-2.5 py-1.5 text-right text-muted-foreground">
                      {formatNumber(stats.samples)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div className="border-t border-border px-5 py-3.5">
        <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">Cost</h3>
        <dl className="mt-1.5">
          <StatLine label="External language model calls" value={formatNumber(cost.external_llm_calls)} />
          <StatLine label="External language model cost" value={cost.external_llm_cost} />
          <StatLine label="Local compute cost" value={cost.local_compute_cost} />
          <StatLine label="Cost per 1000 decisions" value={cost.cost_per_1000_decisions} />
          <StatLine label="Dollar-weighted recall" value={cost.dollar_weighted_recall} />
        </dl>
        {cost.price_basis ? (
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            Price basis: {cost.price_basis}
          </p>
        ) : null}
      </div>

      {sweeps.length > 0 ? (
        <div className="border-t border-border px-5 py-3.5">
          <Disclosure summary={`Concurrency sweeps (${sweeps.length})`}>
            {sweeps.map(([name, sweep]) => (
              <div key={name} className="space-y-1">
                <p className="font-mono text-[11px] text-foreground">
                  {name} · {sweep.endpoint} · peak {sweep.peak_throughput_rps.toFixed(1)} rps ·{" "}
                  {sweep.workers} workers
                </p>
                <table className="w-full border-collapse text-left text-[11.5px]">
                  <caption className="sr-only">Concurrency sweep for {name}</caption>
                  <thead>
                    <tr className="text-[10px] tracking-[0.04em] text-muted-foreground uppercase">
                      <th scope="col" className="py-1">Concurrency</th>
                      <th scope="col" className="py-1 text-right">Throughput</th>
                      <th scope="col" className="py-1 text-right">p95</th>
                      <th scope="col" className="py-1 text-right">Error rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sweep.levels.map((level) => (
                      <tr key={`${name}-${level.concurrency}`} className="border-t border-border/60">
                        <td className="py-1">{level.concurrency}</td>
                        <td className="tabular py-1 text-right">{level.throughput_rps.toFixed(1)} rps</td>
                        <td className="tabular py-1 text-right">{formatLatency(level.p95_ms)}</td>
                        <td className="tabular py-1 text-right">
                          {formatFraction(level.error_rate, 2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </Disclosure>
        </div>
      ) : null}
    </Card>
  );
}
