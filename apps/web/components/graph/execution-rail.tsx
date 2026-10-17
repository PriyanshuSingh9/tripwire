"use client";

import { useState } from "react";
import {
  CheckCircle2,
  CircleDashed,
  MinusCircle,
  PlugZap,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import {
  STAGE_STATUS_LABEL,
  type ExecutionStage,
  type StageStatus,
} from "@/lib/graph/stages";

const STATUS_ICON: Record<StageStatus, React.ComponentType<{ className?: string }>> = {
  completed: CheckCircle2,
  waiting: CircleDashed,
  failed: XCircle,
  not_run: MinusCircle,
  unavailable: PlugZap,
};

const STATUS_ICON_CLASS: Record<StageStatus, string> = {
  completed: "text-safe",
  waiting: "text-muted-foreground",
  failed: "text-critical",
  not_run: "text-muted-foreground",
  unavailable: "text-muted-foreground",
};

/**
 * The execution rail: the eight stages of one investigation, each read from a
 * field of the response. Selecting a stage reveals its detail below the strip,
 * so the rail stays compact while the facts stay reachable.
 */
export function ExecutionRail({
  stages,
  running,
  runningLabel,
}: {
  stages: ExecutionStage[];
  running: boolean;
  runningLabel?: string;
}): React.JSX.Element {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = stages.find((stage) => stage.id === selectedId) ?? stages[0];

  return (
    <section aria-label="Investigation execution stages" className="space-y-2.5">
      {running ? (
        <p role="status" className="flex items-center gap-2 text-[12px] text-muted-foreground">
          <CircleDashed className="size-3.5 animate-spin" aria-hidden="true" />
          {runningLabel ?? "The investigation request is in flight."}
        </p>
      ) : null}

      <ol className="flex flex-wrap items-stretch gap-1.5">
        {stages.map((stage, index) => {
          const Icon = STATUS_ICON[stage.status];
          const active = selected?.id === stage.id;
          return (
            <li key={stage.id} className="flex items-stretch">
              <button
                type="button"
                aria-pressed={active}
                onClick={() => setSelectedId(stage.id)}
                className={cn(
                  "flex min-w-[104px] items-center gap-2 rounded-control border px-2.5 py-2 text-left transition-colors duration-[var(--motion-fast)]",
                  active
                    ? "border-border-strong bg-surface-active"
                    : "border-border bg-muted hover:bg-surface-hover",
                )}
              >
                <Icon
                  className={cn("size-3.5 shrink-0", STATUS_ICON_CLASS[stage.status])}
                  aria-hidden="true"
                />
                <span className="min-w-0">
                  <span className="block truncate text-[11.5px] font-medium text-foreground">
                    {stage.label}
                  </span>
                  <span className="block text-[10.5px] text-muted-foreground">
                    {STAGE_STATUS_LABEL[stage.status]}
                  </span>
                </span>
              </button>
              {index < stages.length - 1 ? (
                <span
                  aria-hidden="true"
                  className="mx-0.5 hidden w-2 self-center border-t border-border sm:block"
                />
              ) : null}
            </li>
          );
        })}
      </ol>

      {selected ? (
        <div
          key={selected.id}
          className="fade-in rounded-control border border-border bg-muted px-3.5 py-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[12.5px] font-medium text-foreground">{selected.label}</p>
            <span className="text-[11px] text-muted-foreground">
              {STAGE_STATUS_LABEL[selected.status]}
            </span>
          </div>
          <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
            {selected.summary}
          </p>
          {selected.facts.length > 0 ? (
            <dl className="mt-2.5 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
              {selected.facts.map((fact) => (
                <div
                  key={`${selected.id}-${fact.label}`}
                  className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1 last:border-b-0"
                >
                  <dt className="text-[11px] text-muted-foreground">{fact.label}</dt>
                  <dd
                    className={cn(
                      "tabular truncate text-right font-mono text-[11px]",
                      fact.tone === "warning"
                        ? "text-warning"
                        : fact.tone === "critical"
                          ? "text-critical"
                          : fact.tone === "safe"
                            ? "text-safe"
                            : fact.tone === "muted"
                              ? "text-muted-foreground"
                              : "text-foreground",
                    )}
                    title={fact.value}
                  >
                    {fact.value}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {selected.caveat ? (
            <p className="mt-2.5 border-l-2 border-border-strong pl-2 text-[11px] leading-relaxed text-muted-foreground">
              {selected.caveat}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
