"use client";

import { useState } from "react";
import Link from "next/link";
import { FolderPlus, Info } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { TextField } from "@/components/ui/controls";
import { ApiError } from "@/lib/api/client";
import { useCreateCase } from "@/lib/api/hooks";
import { useAnalystId, setAnalystId } from "@/lib/analyst";

/**
 * Open a case for a scored transaction.
 *
 * The API accepts a case only for a transaction present in the score artifact
 * and allows at most one active case per transaction; both refusals are shown
 * as what they are rather than retried into a success.
 */
export function OpenCaseCard({
  transactionId,
  caseId,
  className,
}: {
  transactionId: string;
  caseId?: string | null;
  className?: string;
}): React.JSX.Element {
  const analystId = useAnalystId();
  const createCase = useCreateCase();
  const [notes, setNotes] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [openedCaseId, setOpenedCaseId] = useState<string | null>(null);

  const effectiveCaseId = openedCaseId ?? caseId ?? null;
  const effectiveAnalyst = analystId.trim();

  const errorMessage = (() => {
    const error = createCase.error;
    if (!error) return null;
    if (error instanceof ApiError) {
      if (error.isConflict) return "A case is already open for this transaction.";
      if (error.isNotFound) return "This transaction is not in the score artifact, so no case can be opened.";
      if (error.isUnavailable) return error.detail;
    }
    return error.message;
  })();

  return (
    <Card className={className}>
      <CardHeader
        title="What happens next"
        description="Cases live in the local case store and carry the score and decision snapshot from opening. Opening one records a review task, not a finding."
        level={2}
      />
      <div className="space-y-3 px-5 py-4">
        {effectiveCaseId ? (
          <div className="rounded-control border border-safe/40 bg-safe/10 px-3 py-2.5">
            <p className="text-[12px] font-medium text-safe">Case record exists</p>
            <p className="mt-1 font-mono text-[11.5px] text-foreground">{effectiveCaseId}</p>
            <Link
              href={`/cases?case_id=${encodeURIComponent(effectiveCaseId)}`}
              className="mt-1.5 inline-block text-[11.5px] font-medium text-primary hover:underline"
            >
              Review the case
            </Link>
          </div>
        ) : (
          <>
            <TextField
              label="Analyst identifier"
              required
              mono
              value={analystId}
              onChange={setAnalystId}
              placeholder="your analyst id"
              hint="Sent as opened_by and stored with the case history. Kept in this browser only."
            />
            <TextField
              label="Opening note"
              value={notes}
              onChange={setNotes}
              placeholder="why this transfer is being looked at"
              hint="Optional. Recorded in the case history."
            />
            <Button
              variant="primary"
              icon={FolderPlus}
              disabled={effectiveAnalyst.length === 0}
              title={
                effectiveAnalyst.length === 0
                  ? "Enter an analyst identifier first"
                  : "Open a case for this transaction"
              }
              onClick={() => setConfirming(true)}
            >
              Open case
            </Button>
            <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              Opening a case records a review task. It is not a finding, and it does not change the
              score or the policy decision.
            </p>
          </>
        )}

        {errorMessage ? (
          <p role="alert" className="rounded-control border border-critical/40 bg-critical/10 px-2.5 py-2 text-[12px] text-critical">
            {errorMessage}
          </p>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirming}
        title="Open a case for this transaction?"
        description={
          <>
            Transaction <span className="font-mono">{transactionId}</span> will be recorded as a
            suspected case under analyst{" "}
            <span className="font-mono">{effectiveAnalyst}</span>. At most one active case per
            transaction is allowed.
          </>
        }
        confirmLabel="Open case"
        pending={createCase.isPending}
        error={errorMessage}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          createCase.mutate(
            {
              transaction_id: transactionId,
              opened_by: effectiveAnalyst,
              notes: notes.trim().length > 0 ? notes.trim() : null,
            },
            {
              onSuccess: (created) => {
                setOpenedCaseId(created.case_id);
                setConfirming(false);
              },
            },
          );
        }}
      />
    </Card>
  );
}
