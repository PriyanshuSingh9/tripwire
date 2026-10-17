"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Cpu,
  Database,
  HardDriveDownload,
  Info,
  PlugZap,
  ServerCog,
  ShieldQuestion,
  Trash2,
} from "lucide-react";

import { Card, CardBody, CardHeader, StatLine } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Chip, StatusBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/ui/states";
import { Disclosure } from "@/components/ui/side-panel";
import { useDependencyProbes } from "@/lib/api/probes";
import { useBenchmarks, useHealth } from "@/lib/api/hooks";
import { clearInvestigations, useInvestigationLog } from "@/lib/runs/log";
import { clearLastRun, useLastRun } from "@/lib/runs/last-result";
import {
  formatDuration,
  formatLatency,
  formatNumber,
  formatRelative,
  formatTimestamp,
} from "@/lib/format";
import type { PerformanceSummary } from "@/lib/api/types";

function probeTone(state: string): "safe" | "critical" | "warning" | "neutral" {
  if (state === "ok") return "safe";
  if (state === "unavailable") return "warning";
  if (state === "failed") return "critical";
  return "neutral";
}

function probeLabel(state: string): string {
  if (state === "ok") return "Available";
  if (state === "loading") return "Probing";
  if (state === "unavailable") return "Unavailable";
  return "Failed";
}

/**
 * System.
 *
 * Everything here is either a probe result, a recorded measurement shipped with
 * the artifacts, or local session data. The page states plainly where a value
 * comes from and what the API does not expose.
 */
export function SystemPage(): React.JSX.Element {
  const health = useHealth(30_000);
  const probes = useDependencyProbes();
  const benchmarks = useBenchmarks();
  const log = useInvestigationLog();
  const lastRun = useLastRun();
  const [storageNotice, setStorageNotice] = useState<string | null>(null);

  const failures = useMemo(() => log.filter((entry) => entry.status === "error"), [log]);
  const performance: PerformanceSummary | null = benchmarks.data?.performance ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Service"
          description="Read from the health endpoint. The service reports its own status and the graph connection, and says whether a language model provider is configured."
          level={2}
          action={
            <Button
              size="sm"
              variant="secondary"
              icon={PlugZap}
              loading={probes.isFetching}
              onClick={probes.refetchAll}
            >
              Re-probe everything
            </Button>
          }
        />
        {health.isPending ? (
          <TableSkeleton rows={3} columns={2} />
        ) : health.isError ? (
          <ErrorState
            error={health.error}
            onRetry={() => void health.refetch()}
            hint="If the service is not running, start it with: uv run --project services/api uvicorn api.app:app --host 127.0.0.1 --port 8000"
          />
        ) : (
          <CardBody className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <dl>
                <StatLine label="status" value={health.data.status} />
                <StatLine
                  label="version"
                  value={<span className="font-mono text-[11px]">{health.data.version}</span>}
                />
              </dl>
              <p className="mt-2 text-[11.5px] leading-relaxed text-muted-foreground">
                The endpoint reports liveness, not a readiness gate over the
                artifacts: a service can be healthy while the score artifact is
                unavailable, because artifact reads happen per request.
              </p>
            </div>
            <div className="space-y-3">
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12px] font-medium text-foreground">
                    FalkorDB connection
                  </span>
                  <StatusBadge
                    label={
                      health.data.falkordb === "available"
                        ? "Available"
                        : health.data.falkordb === "not_configured"
                          ? "Not configured"
                          : "Unavailable"
                    }
                    tone={
                      health.data.falkordb === "available"
                        ? "safe"
                        : health.data.falkordb === "not_configured"
                          ? "neutral"
                          : "warning"
                    }
                    icon={Database}
                  />
                </div>
                <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                  Probed with a PING over a one second timeout on every health
                  request. A configured host that does not answer is reported as
                  unavailable rather than as an error.
                </p>
              </div>
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[12px] font-medium text-foreground">Language model provider</span>
                  <StatusBadge
                    label={
                      health.data.llm === "configured" ? "Configured" : "Not configured"
                    }
                    tone={health.data.llm === "configured" ? "safe" : "neutral"}
                    icon={Cpu}
                  />
                </div>
                <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
                  Reports configuration only. No request is sent to the provider,
                  so this is not a statement that the provider will answer.
                  {health.data.llm === "not_configured"
                    ? " Without a provider, the report agent cannot run and the deterministic report is used."
                    : " With a provider configured, the report agent is still opt-in per run, and any failure falls back to the deterministic report."}
                </p>
              </div>
            </div>
          </CardBody>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Dependency probes"
          description="Each row reads one real endpoint. A probe never infers another dependency's state."
          level={2}
          action={
            <Button
              size="sm"
              variant="ghost"
              icon={PlugZap}
              loading={probes.isFetching}
              onClick={probes.refetchAll}
            >
              Re-probe
            </Button>
          }
        />
        <ul className="divide-y divide-border">
          {probes.probes.map((probe) => (
            <li key={probe.id} className="px-5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[12.5px] font-medium text-foreground">{probe.label}</span>
                <StatusBadge label={probeLabel(probe.state)} tone={probeTone(probe.state)} announce />
              </div>
              <p className="mt-0.5 font-mono text-[10.5px] text-muted-foreground">{probe.reads}</p>
              <p className="mt-1 text-[11.5px] leading-relaxed text-foreground">{probe.detail}</p>
              {probe.remedy ? (
                <p className="mt-1 text-[11.5px] leading-relaxed text-warning">{probe.remedy}</p>
              ) : null}
            </li>
          ))}
        </ul>
        <div className="border-t border-border px-5 py-3">
          <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
            <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            A 503 from an endpoint means a required artifact or dependency is
            missing; the service returns its own detail string, shown above verbatim.
            Nothing is retried into a success.
          </p>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Recorded runtime"
            description="Captured with the published benchmark run. These are not live measurements of this deployment."
            level={2}
            action={
              <Link
                href="/reports?tab=reports"
                className="text-[11.5px] font-medium text-primary hover:underline"
              >
                Full benchmark report
              </Link>
            }
          />
          {benchmarks.isPending ? (
            <TableSkeleton rows={4} columns={2} />
          ) : benchmarks.isError ? (
            <EmptyState
              title="Runtime report unavailable"
              description="The performance report is published as part of the benchmark artifacts. It is not readable right now, so no runtime figure is shown."
            />
          ) : performance === null ? (
            <EmptyState
              title="No runtime report in the artifact"
              description="The benchmark artifact contains detection metrics but no performance report."
            />
          ) : (
            <>
              <CardBody className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <dl>
                  <StatLine label="Measured on" value={performance.measured_on} />
                  <StatLine label="Dataset" value={performance.dataset_label} />
                  <StatLine label="Graph scope" value={performance.graph_scope} />
                  <StatLine label="Execution" value={performance.execution_mode} />
                </dl>
                <dl>
                  <StatLine label="Hardware" value={performance.hardware} />
                  <StatLine
                    label="Run id"
                    value={performance.run_id ?? "not recorded"}
                    tone="muted"
                  />
                  <StatLine
                    label="Git commit"
                    value={
                      <span className="font-mono text-[11px]">
                        {performance.git_commit ?? "not recorded"}
                      </span>
                    }
                    tone="muted"
                  />
                </dl>
              </CardBody>
              <div className="border-t border-border">
                <table className="w-full border-collapse text-left text-[12px]">
                  <caption className="sr-only">Recorded endpoint latency</caption>
                  <thead>
                    <tr className="border-b border-border text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                      <th scope="col" className="px-5 py-1.5">
                        Endpoint
                      </th>
                      <th scope="col" className="px-5 py-1.5 text-right">
                        p50
                      </th>
                      <th scope="col" className="px-5 py-1.5 text-right">
                        p95
                      </th>
                      <th scope="col" className="px-5 py-1.5 text-right">
                        Samples
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(performance.api).map(([name, stats]) => (
                      <tr key={name} className="border-b border-border/70 last:border-b-0">
                        <th
                          scope="row"
                          className="px-5 py-1.5 font-mono text-[11px] font-medium text-foreground"
                        >
                          {name}
                        </th>
                        <td className="tabular px-5 py-1.5 text-right">
                          {formatLatency(stats.p50_ms)}
                        </td>
                        <td className="tabular px-5 py-1.5 text-right">
                          {formatLatency(stats.p95_ms)}
                        </td>
                        <td className="tabular px-5 py-1.5 text-right text-muted-foreground">
                          {formatNumber(stats.samples)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="border-t border-border px-5 py-3">
                <Disclosure summary="Graph query and inference latency">
                  <dl>
                    {Object.entries(performance.graph_queries).map(([name, stats]) => (
                      <StatLine
                        key={name}
                        label={name}
                        value={`${formatLatency(stats.p50_ms)} p50 · ${formatLatency(stats.p95_ms)} p95 · ${formatNumber(stats.samples)} samples`}
                        tone="muted"
                      />
                    ))}
                    <StatLine
                      label="single prepared row"
                      value={`${formatLatency(performance.model_inference.single_prepared_row.p50_ms)} p50 · ${formatLatency(performance.model_inference.single_prepared_row.p95_ms)} p95`}
                      tone="muted"
                    />
                    <StatLine
                      label="full test cohort"
                      value={`${formatNumber(performance.model_inference.full_test_cohort.rows)} rows in ${formatDuration(
                        performance.model_inference.full_test_cohort.median_seconds * 1000,
                      )}`}
                      tone="muted"
                    />
                  </dl>
                </Disclosure>
              </div>
            </>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Session data in this browser"
            description="The service stores no run history, so these are the only records of past runs. They live in this browser only."
            level={2}
            action={
              <Button
                size="sm"
                variant="ghost"
                icon={Trash2}
                onClick={() => {
                  clearInvestigations();
                  clearLastRun();
                  setStorageNotice(
                    "Local run log and cached run cleared. This does not touch the case store or any artifact.",
                  );
                }}
              >
                Clear local data
              </Button>
            }
          />
          <CardBody className="space-y-2">
            <dl>
              <StatLine label="Runs logged" value={formatNumber(log.length)} />
              <StatLine
                label="Failed runs logged"
                value={formatNumber(failures.length)}
                tone={failures.length > 0 ? "warning" : "muted"}
              />
              <StatLine
                label="Last run cached"
                value={
                  lastRun
                    ? `${lastRun.response.transaction_id} · ${formatRelative(lastRun.at)}`
                    : "none in this tab"
                }
                tone="muted"
              />
              {lastRun ? (
                <StatLine
                  label="Cached investigation"
                  value={
                    <span className="font-mono text-[11px]">
                      {lastRun.response.investigation_id}
                    </span>
                  }
                  tone="muted"
                />
              ) : null}
            </dl>
            <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
              <HardDriveDownload className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              The run log is kept in local storage (last 50 attempts) and the
              cached run in session storage. Both hold real responses and real
              errors only. Clearing them changes nothing on the server.
            </p>
            {storageNotice ? (
              <p role="status" className="text-[11.5px] text-safe">
                {storageNotice}
              </p>
            ) : null}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Recent execution failures"
          description="Failed runs this browser observed, with the service's own status and detail."
          level={2}
          action={<Chip>{failures.length} recorded</Chip>}
        />
        {failures.length === 0 ? (
          <EmptyState
            title="No failed run recorded here"
            description="This list only covers runs started from this browser. It says nothing about other clients, and an empty list is not a health check."
          />
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full border-collapse text-left text-[12px]">
              <caption className="sr-only">Local record of failed investigation runs</caption>
              <thead>
                <tr className="border-b border-border text-[10.5px] tracking-[0.04em] text-muted-foreground uppercase">
                  <th scope="col" className="px-5 py-1.5">
                    When
                  </th>
                  <th scope="col" className="px-5 py-1.5">
                    Transaction
                  </th>
                  <th scope="col" className="px-5 py-1.5">
                    Status
                  </th>
                  <th scope="col" className="px-5 py-1.5">
                    Detail
                  </th>
                  <th scope="col" className="px-5 py-1.5 text-right">
                    Duration
                  </th>
                </tr>
              </thead>
              <tbody>
                {failures.map((entry) => (
                  <tr key={entry.attempt_id} className="border-b border-border/70 last:border-b-0">
                    <td className="px-5 py-1.5 whitespace-nowrap text-muted-foreground">
                      {formatTimestamp(entry.attempted_at)}
                    </td>
                    <td className="px-5 py-1.5 font-mono text-[11px] text-foreground">
                      {entry.transaction_id}
                    </td>
                    <td className="px-5 py-1.5">
                      <StatusBadge
                        label={
                          entry.http_status === null ? "no response" : `HTTP ${entry.http_status}`
                        }
                        tone={entry.http_status === null ? "critical" : "warning"}
                      />
                    </td>
                    <td className="px-5 py-1.5 text-foreground">{entry.error_detail}</td>
                    <td className="tabular px-5 py-1.5 text-right text-muted-foreground">
                      {formatDuration(entry.client_duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Not exposed by the service"
          description="Diagnostic values that are deliberately absent from every response. The workstation does not guess them."
          level={2}
          action={<Chip>by contract</Chip>}
        />
        <CardBody className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <ul className="space-y-2 text-[12px] leading-relaxed text-muted-foreground">
            <li className="flex gap-2">
              <ShieldQuestion className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Graph name, FalkorDB host and port: the health endpoint reports a
              coarse status instead of connection details.
            </li>
            <li className="flex gap-2">
              <ShieldQuestion className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Artifact file paths and the review threshold: availability is
              observable through the endpoints that read them; the threshold is
              reflected only through the decisions themselves.
            </li>
            <li className="flex gap-2">
              <ShieldQuestion className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Language model provider, model name and key status: the health endpoint reports
              configured or not configured, nothing finer.
            </li>
          </ul>
          <ul className="space-y-2 text-[12px] leading-relaxed text-muted-foreground">
            <li className="flex gap-2">
              <ServerCog className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Run, event and alert history: there is no endpoint that lists past
              investigations, which is why Home and System report only local
              activity.
            </li>
            <li className="flex gap-2">
              <ServerCog className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Per-record query durations: tool traces carry one duration per tool
              run, not per returned record, so the evidence table does not claim
              per-row timings.
            </li>
            <li className="flex gap-2">
              <ServerCog className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Dataset ground truth: target labels and laundering typology truth are
              kept out of the inference graph and out of the contracts entirely.
            </li>
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
