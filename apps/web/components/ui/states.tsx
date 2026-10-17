import {
  AlertTriangle,
  Inbox,
  PlugZap,
  RefreshCw,
  ServerCrash,
  TimerOff,
  WifiOff,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";

export function Skeleton({ className }: { className?: string }): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      className={cn("skeleton-pulse rounded-md bg-surface-hover", className)}
    />
  );
}

/** Table-shaped placeholder. Shape matches the content so nothing jumps. */
export function TableSkeleton({
  rows = 6,
  columns = 5,
}: {
  rows?: number;
  columns?: number;
}): React.JSX.Element {
  return (
    <div className="space-y-2 p-4" aria-hidden="true">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-3">
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={cn("h-4", columnIndex === 0 ? "w-40" : "flex-1")}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton({ className }: { className?: string }): React.JSX.Element {
  return (
    <div className={cn("space-y-3 p-5", className)} aria-hidden="true">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-7 w-32" />
      <Skeleton className="h-3 w-40" />
    </div>
  );
}

export function InlineLoading({ label }: { label: string }): React.JSX.Element {
  return (
    <p className="flex items-center gap-2 p-5 text-[12px] text-muted-foreground" role="status">
      <RefreshCw className="size-3.5 animate-spin" aria-hidden="true" />
      {label}
    </p>
  );
}

/**
 * Every empty, failed and unavailable surface states what happened, what it
 * means, and the one thing the reader can do next.
 */
export function StateMessage({
  icon: Icon,
  title,
  description,
  action,
  tone = "neutral",
  className,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  tone?: "neutral" | "warning" | "critical";
  className?: string;
}): React.JSX.Element {
  const toneClass =
    tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : "text-muted-foreground";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 px-6 py-10 text-center",
        className,
      )}
    >
      <Icon className={cn("size-5", toneClass)} aria-hidden="true" />
      <p className="text-[13px] font-medium text-foreground">{title}</p>
      {description ? (
        <div className="max-w-md text-[12px] leading-relaxed text-muted-foreground">
          {description}
        </div>
      ) : null}
      {action ? <div className="mt-1 flex flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}

/** No records exist for the current query. Distinct from a failed request. */
export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}): React.JSX.Element {
  return (
    <StateMessage
      icon={Inbox}
      title={title}
      description={description}
      action={action}
      className={className}
    />
  );
}

function iconForStatus(status: number): React.ComponentType<{ className?: string }> {
  if (status === 0) return WifiOff;
  if (status === 503) return PlugZap;
  if (status === 504) return TimerOff;
  if (status >= 500) return ServerCrash;
  return AlertTriangle;
}

function titleForStatus(status: number): string {
  if (status === 0) return "The service did not respond";
  if (status === 503) return "Required data is not available";
  if (status === 504) return "The request timed out";
  if (status === 404) return "Not found";
  if (status === 409) return "Conflicting state";
  if (status === 422) return "The request was rejected";
  return "The request failed";
}

/**
 * Failure surface. It shows the API's own fixed detail string as the
 * human-readable reason and keeps the status code visible for developers.
 */
export function ErrorState({
  error,
  onRetry,
  retryLabel = "Retry",
  hint,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
  hint?: React.ReactNode;
  className?: string;
}): React.JSX.Element {
  const apiError = error instanceof ApiError ? error : null;
  const status = apiError?.status ?? -1;
  const detail = apiError ? apiError.detail : error instanceof Error ? error.message : "Unknown error";
  const isUnavailable = apiError !== null && (apiError.isUnavailable || apiError.isNetworkError);
  return (
    <StateMessage
      icon={iconForStatus(status)}
      tone={isUnavailable ? "warning" : "critical"}
      title={titleForStatus(status)}
      className={className}
      description={
        <div className="space-y-1.5">
          <p>{detail}</p>
          {hint ? <p className="text-[11px]">{hint}</p> : null}
          <p className="font-mono text-[11px] text-muted-foreground/80">
            {status === 0 ? "no HTTP response" : `HTTP ${status}`}
          </p>
        </div>
      }
      action={
        onRetry ? <Button size="sm" variant="secondary" icon={RefreshCw} onClick={onRetry}>{retryLabel}</Button> : null
      }
    />
  );
}

/**
 * A capability the backend does not expose. It is deliberately not styled as
 * an error: the feature is absent, not broken.
 */
export function UnavailableState({
  title,
  description,
  className,
  action,
}: {
  title: string;
  description?: React.ReactNode;
  className?: string;
  action?: React.ReactNode;
}): React.JSX.Element {
  return (
    <StateMessage
      icon={PlugZap}
      tone="neutral"
      title={title}
      description={description}
      action={action}
      className={className}
    />
  );
}
