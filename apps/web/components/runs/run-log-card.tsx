"use client";

import { History, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Chip, StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/states";
import { formatDuration, formatRelative, formatScore } from "@/lib/format";
import { clearInvestigations, type InvestigationLogEntry } from "@/lib/runs/log";

/**
 * This browser's investigation log.
 *
 * The API exposes no run history, so this is the only truthful source for
 * "recent activity" and "recent failures". Every row was written from a real
 * response or a real HTTP error on this machine.
 */
export function RunLogCard({
  entries,
  onRerun,
  limit = 8,
  showClear = true,
  className,
  title = "Recent runs in this browser",
}: {
  entries: InvestigationLogEntry[];
  onRerun?: (transactionId: string) => void;
  limit?: number;
  showClear?: boolean;
  className?: string;
  title?: string;
}): React.JSX.Element {
  const visible = entries.slice(0, limit);
  return (
    <Card className={className}>
      <CardHeader
        title={title}
        description="Recorded locally from real responses and real errors. The service stores no run history."
        level={2}
        action={
          showClear && entries.length > 0 ? (
            <Button
              size="sm"
              variant="ghost"
              icon={Trash2}
              onClick={() => clearInvestigations()}
              title="Remove the local log entries"
            >
              Clear
            </Button>
          ) : (
            <Chip>{entries.length} logged</Chip>
          )
        }
      />
      {visible.length === 0 ? (
        <EmptyState
          title="No runs recorded in this browser yet"
          description="Runs started from this browser are listed here with their real outcome, latency and any HTTP error."
        />
      ) : (
        <ul className="divide-y divide-border">
          {visible.map((entry) => (
            <li key={entry.attempt_id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
              <StatusBadge
                label={entry.status === "ok" ? "Completed" : "Failed"}
                tone={entry.status === "ok" ? "safe" : "critical"}
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[11.5px] text-foreground">
                  {entry.transaction_id}
                </p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                  {entry.status === "ok"
                    ? `${entry.outcome?.replace(/_/g, " ") ?? "outcome not reported"} · ${
                        entry.risk_score !== null ? formatScore(entry.risk_score) : "score n/a"
                      } · ${entry.evidence_counts?.paths ?? 0} paths`
                    : `${entry.http_status === null ? "no HTTP response" : `HTTP ${entry.http_status}`} · ${
                        entry.error_detail ?? "unknown error"
                      }`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] whitespace-nowrap text-muted-foreground">
                  {formatDuration(entry.client_duration_ms)} · {formatRelative(entry.attempted_at)}
                </span>
                {onRerun ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={History}
                    onClick={() => onRerun(entry.transaction_id)}
                  >
                    Re-run
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
