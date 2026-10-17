"use client";

import { Card, CardFooter, CardHeader } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Shared chart chrome so every figure reads the same way. */

export const AXIS_STYLE = {
  fontSize: 10.5,
  fill: "#858985",
} as const;

export const GRID_STYLE = {
  stroke: "rgba(230,231,229,0.07)",
  strokeDasharray: "2 4",
} as const;

export const TOOLTIP_STYLE = {
  backgroundColor: "#141515",
  border: "1px solid rgba(230,231,229,0.18)",
  borderRadius: 8,
  fontSize: 11.5,
  color: "#E6E7E5",
  padding: "6px 8px",
} as const;

export const TOOLTIP_LABEL_STYLE = {
  color: "#858985",
  fontSize: 11,
  marginBottom: 2,
} as const;

/**
 * Tooltip formatter shared by every figure on the workstation.
 *
 * Metric values are probabilities, so four decimals is the resolution at which
 * published artifacts are quoted. A non-numeric value is passed through
 * unchanged instead of being coerced into a number the data does not carry.
 */
export function formatChartValue(value: unknown): string {
  if (typeof value !== "number") return value === undefined || value === null ? "—" : String(value);
  return Number.isFinite(value) ? value.toFixed(4) : "—";
}

export const CHART_COLORS = {
  primary: "#F28C38",
  safe: "#78B887",
  warning: "#E9B44C",
  critical: "#EF6A62",
  muted: "#858985",
  raised: "#3A3D3A",
} as const;

export function ChartCard({
  title,
  description,
  action,
  caption,
  children,
  className,
  height = 220,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  caption?: string;
  children: React.ReactNode;
  className?: string;
  height?: number;
}): React.JSX.Element {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader title={title} description={description} level={2} action={action} />
      <div className="px-3 py-3" style={{ height }}>
        {children}
      </div>
      {caption ? <CardFooter>{caption}</CardFooter> : null}
    </Card>
  );
}

/**
 * A legend that states the series in words, for figures whose meaning must
 * survive without colour. It duplicates the chart legend on purpose; the chart
 * itself carries no interaction that the table below does not.
 */
export function ChartKey({
  items,
}: {
  items: { label: string; color: string }[];
}): React.JSX.Element {
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block size-2.5 rounded-[3px]"
            style={{ backgroundColor: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
