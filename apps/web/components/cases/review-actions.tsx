"use client";

import { useState } from "react";
import { CheckCircle2, Info, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { TextField } from "@/components/ui/controls";
import { ApiError } from "@/lib/api/client";
import { useReviewCase } from "@/lib/api/hooks";
import { setAnalystId, useAnalystId } from "@/lib/analyst";
import type { CaseItem, ReviewAction } from "@/lib/api/types";

/**
 * Terminal analyst decisions.
 *
 * Confirm and dismiss are explicit, provenance-carrying actions. Both are
 * irreversible for this case record, so each goes through a confirmation that
 * restates the consequence; a refused review is shown as a conflict instead of
 * being retried.
 */
export function ReviewActions({ caseItem }: { caseItem: CaseItem }): React.JSX.Element {
  const analystId = useAnalystId();
  const review = useReviewCase();
  const [notes, setNotes] = useState("");
  const [patternType, setPatternType] = useState("");
  const [pendingAction, setPendingAction] = useState<ReviewAction | null>(null);

  const effectiveAnalyst = analystId.trim();
  const terminal = caseItem.status === "confirmed" || caseItem.status === "dismissed";

  const errorMessage = (() => {
    const error = review.error;
    if (!error) return null;
    if (error instanceof ApiError) {
      if (error.isConflict) {
        return "This case already carries a terminal decision. A review is a decision, never an overwrite.";
      }
      if (error.isNotFound) return "The case no longer exists in the local case store.";
    }
    return error.message;
  })();

  if (terminal) {
    return (
      <div className="space-y-2 rounded-control border border-border bg-muted px-3.5 py-3">
        <p className="text-[12px] font-medium text-foreground">
          This case is {caseItem.status}. Reviews are terminal.
        </p>
        <p className="text-[11.5px] leading-relaxed text-muted-foreground">
          The decision and its reviewer are recorded in the history below. A new case can be opened
          for the same transaction only after the record is dismissed, and the history stays.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3.5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <TextField
          label="Analyst identifier"
          required
          mono
          value={analystId}
          onChange={setAnalystId}
          placeholder="your analyst id"
          hint="Recorded as the reviewer of this decision."
        />
        <TextField
          label="Pattern noted by the analyst"
          value={patternType}
          onChange={setPatternType}
          placeholder="optional, your own words"
          hint="Free text. The system does not supply a typology list."
        />
      </div>
      <TextField
        label="Decision note"
        value={notes}
        onChange={setNotes}
        placeholder="what supports this decision"
        hint="Optional. Stored in the case history with your identifier."
      />

      {errorMessage ? (
        <p role="alert" className="rounded-control border border-critical/40 bg-critical/10 px-2.5 py-2 text-[12px] text-critical">
          {errorMessage}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="danger"
          icon={XCircle}
          disabled={effectiveAnalyst.length === 0}
          title={
            effectiveAnalyst.length === 0
              ? "Enter an analyst identifier first"
              : "Record an explicit dismissal"
          }
          onClick={() => setPendingAction("dismiss")}
        >
          Dismiss case
        </Button>
        <Button
          variant="primary"
          icon={CheckCircle2}
          disabled={effectiveAnalyst.length === 0}
          title={
            effectiveAnalyst.length === 0
              ? "Enter an analyst identifier first"
              : "Record an explicit confirmation"
          }
          onClick={() => setPendingAction("confirm")}
        >
          Confirm case
        </Button>
        <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          Confirming records an analyst judgement. It does not change the model score, the policy
          decision, or any payment.
        </p>
      </div>

      <ConfirmDialog
        open={pendingAction !== null}
        title={pendingAction === "confirm" ? "Confirm this case?" : "Dismiss this case?"}
        description={
          pendingAction === "confirm" ? (
            <>
              Case <span className="font-mono">{caseItem.case_id}</span> for transaction{" "}
              <span className="font-mono">{caseItem.transaction_id}</span> will be recorded as
              confirmed by <span className="font-mono">{effectiveAnalyst}</span>. This cannot be
              reversed for this record.
            </>
          ) : (
            <>
              Case <span className="font-mono">{caseItem.case_id}</span> will be recorded as
              dismissed by <span className="font-mono">{effectiveAnalyst}</span>. The history and
              evidence stay readable, and a new case can be opened later.
            </>
          )
        }
        confirmLabel={pendingAction === "confirm" ? "Confirm case" : "Dismiss case"}
        confirmVariant={pendingAction === "confirm" ? "primary" : "danger"}
        pending={review.isPending}
        error={errorMessage}
        onCancel={() => setPendingAction(null)}
        onConfirm={() => {
          if (pendingAction === null) return;
          review.mutate(
            {
              caseId: caseItem.case_id,
              body: {
                action: pendingAction,
                reviewer_id: effectiveAnalyst,
                pattern_type: patternType.trim().length > 0 ? patternType.trim() : null,
                notes: notes.trim().length > 0 ? notes.trim() : null,
              },
            },
            { onSuccess: () => setPendingAction(null) },
          );
        }}
      />
    </div>
  );
}
