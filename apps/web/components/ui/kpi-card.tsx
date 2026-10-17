"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/utils";

export type KpiState = "ready" | "loading" | "empty" | "unavailable" | "error";

/**
 * One headline figure with the context needed to read it honestly.
 *
 * The value pops in only when it changes after the first render, so the motion
 * marks a real recomputation instead of decorating the initial paint.
 */
export function KpiCard({
  label,
  value,
  unit,
  supporting,
  state = "ready",
  stateLabel,
  href,
  hrefLabel,
  icon: Icon,
  className,
  emphasis = false,
}: {
  label: string;
  value: string | number | null;
  unit?: string;
  supporting?: React.ReactNode;
  state?: KpiState;
  stateLabel?: string;
  href?: string;
  hrefLabel?: string;
  icon?: React.ComponentType<{ className?: string }>;
  className?: string;
  emphasis?: boolean;
}): React.JSX.Element {
  const [pulse, setPulse] = useState(false);
  const previous = useRef<string | number | null>(value);

  useEffect(() => {
    if (previous.current !== value && previous.current !== null) {
      setPulse(true);
      const timer = window.setTimeout(() => setPulse(false), 340);
      previous.current = value;
      return () => window.clearTimeout(timer);
    }
    previous.current = value;
    return undefined;
  }, [value]);

  const showValue = state === "ready" && value !== null;

  const body = (
    <Card
      className={cn(
        "relative flex h-full flex-col justify-between gap-3 overflow-hidden px-4 py-3.5 transition-colors duration-[var(--motion-fast)]",
        href && "hover:border-border-strong hover:bg-surface-hover",
        emphasis && "border-primary/30",
        className,
      )}
    >
      {emphasis ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(180deg, rgba(242, 140, 56, 0.14), rgba(242, 140, 56, 0.03) 46%, rgba(242, 140, 56, 0) 72%)",
          }}
        />
      ) : null}
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] font-medium tracking-[0.04em] text-muted-foreground uppercase">
          {label}
        </p>
        {Icon ? <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}
      </div>

      <div className="min-h-8">
        {state === "loading" ? (
          <Skeleton className="h-7 w-24" />
        ) : showValue ? (
          <p
            className={cn(
              "tabular text-[26px] leading-none font-semibold tracking-[-0.02em]",
              emphasis ? "text-primary" : "text-foreground",
              pulse && "number-pop-in",
            )}
          >
            {value}
            {unit ? (
              <span className="ml-1 text-[13px] font-medium text-muted-foreground">{unit}</span>
            ) : null}
          </p>
        ) : (
          <p className="text-[13px] leading-tight font-medium text-muted-foreground">
            {stateLabel ??
              (state === "unavailable"
                ? "Not available"
                : state === "empty"
                  ? "None"
                  : "Not measured")}
          </p>
        )}
      </div>

      <div className="space-y-1">
        {supporting ? (
          <div className="text-[11.5px] leading-relaxed text-muted-foreground">{supporting}</div>
        ) : null}
        {href && hrefLabel ? (
          <p className="text-[11px] font-medium text-primary">{hrefLabel}</p>
        ) : null}
      </div>
    </Card>
  );

  if (href && state !== "loading") {
    return (
      <Link href={href} className="block h-full rounded-card">
        {body}
      </Link>
    );
  }
  return body;
}

/** Compact metric readout used inside cards and tables. */
export function MetricValue({
  label,
  value,
  unit,
  tone = "default",
  hint,
}: {
  label: string;
  value: string;
  unit?: string;
  tone?: "default" | "muted" | "primary" | "warning" | "critical" | "safe";
  hint?: string;
}): React.JSX.Element {
  const toneClass =
    tone === "primary"
      ? "text-primary"
      : tone === "warning"
        ? "text-warning"
        : tone === "critical"
          ? "text-critical"
          : tone === "safe"
            ? "text-safe"
            : tone === "muted"
              ? "text-muted-foreground"
              : "text-foreground";
  return (
    <div title={hint}>
      <p className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">{label}</p>
      <p className={cn("tabular mt-1 text-[20px] leading-none font-semibold", toneClass)}>
        {value}
        {unit ? (
          <span className="ml-1 text-[12px] font-medium text-muted-foreground">{unit}</span>
        ) : null}
      </p>
    </div>
  );
}
