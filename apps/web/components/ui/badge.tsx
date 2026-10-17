import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type BadgeTone = "neutral" | "primary" | "safe" | "warning" | "critical";

const TONE_CLASS: Record<BadgeTone, string> = {
  neutral: "border-border-strong bg-surface-hover text-muted-foreground",
  primary: "border-primary/40 bg-primary/12 text-primary",
  safe: "border-safe/40 bg-safe/12 text-safe",
  warning: "border-warning/40 bg-warning/12 text-warning",
  critical: "border-critical/40 bg-critical/12 text-critical",
};

export interface StatusBadgeProps {
  label: string;
  tone: BadgeTone;
  icon?: LucideIcon;
  className?: string;
  /** Rolling/announcing behaviour is only needed where a state really changes. */
  announce?: boolean;
  title?: string;
}

/**
 * Status is always an icon plus a word: colour never carries meaning alone,
 * so the badge still reads in monochrome or with a colour-vision difference.
 */
export function StatusBadge({
  label,
  tone,
  icon: Icon,
  className,
  announce = false,
  title,
}: StatusBadgeProps): React.JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap",
        TONE_CLASS[tone],
        className,
      )}
      title={title}
      {...(announce ? { role: "status", "aria-live": "polite" as const } : {})}
    >
      {Icon ? <Icon className="size-3 shrink-0" aria-hidden="true" /> : null}
      {label}
    </span>
  );
}

/** Small metadata chip, for counts and identifiers next to a heading. */
export function Chip({
  children,
  className,
  mono = false,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  mono?: boolean;
  title?: string;
}): React.JSX.Element {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center rounded-md border border-border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground",
        mono && "font-mono text-[11px]",
        className,
      )}
    >
      {children}
    </span>
  );
}
