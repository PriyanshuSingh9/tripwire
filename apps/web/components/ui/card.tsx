import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Elevated surface used for every panel, table and card on all six pages. */
export function Card({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      {...rest}
      className={cn(
        "rounded-card border border-border bg-card surface-raised",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  action,
  className,
  headingId,
  level = 2,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  headingId?: string;
  level?: 2 | 3;
}): React.JSX.Element {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-3.5",
        className,
      )}
    >
      <div className="min-w-0">
        <Heading
          id={headingId}
          className="text-[13px] font-semibold tracking-[0.02em] text-foreground"
        >
          {title}
        </Heading>
        {description ? (
          <p className="mt-1 max-w-prose text-[12px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}

export function CardBody({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div {...rest} className={cn("px-5 py-4", className)}>
      {children}
    </div>
  );
}

export function CardFooter({
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      {...rest}
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 border-t border-border px-5 py-3 text-[12px] text-muted-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** A labelled figure inside a card body: label on the leading edge, value trailing. */
export function StatLine({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "muted" | "warning" | "critical" | "safe";
}): React.JSX.Element {
  const toneClass =
    tone === "muted"
      ? "text-muted-foreground"
      : tone === "warning"
        ? "text-warning"
        : tone === "critical"
          ? "text-critical"
          : tone === "safe"
            ? "text-safe"
            : "text-foreground";
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="min-w-0 text-[12px] text-muted-foreground">
        {label}
        {hint ? <span className="ml-1 text-[11px] opacity-80">{hint}</span> : null}
      </dt>
      <dd className={cn("tabular min-w-0 text-right text-[13px] font-medium break-words", toneClass)}>{value}</dd>
    </div>
  );
}
