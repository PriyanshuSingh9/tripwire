"use client";

import { Play, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import {
  CheckboxField,
  SelectField,
  TextField,
} from "@/components/ui/controls";

export interface RunFormState {
  transactionId: string;
  maxHops: string;
  timeWindowSeconds: string;
  resultLimit: string;
  useLlm: boolean;
  act: boolean;
}

export const DEFAULT_RUN_FORM: RunFormState = {
  transactionId: "",
  maxHops: "2",
  timeWindowSeconds: "86400",
  resultLimit: "50",
  useLlm: false,
  act: false,
};

const HOP_OPTIONS = [
  { value: "1", label: "1 hop: direct counterparties" },
  { value: "2", label: "2 hops: one intermediary" },
  { value: "3", label: "3 hops: the widest allowed" },
];

const WINDOW_OPTIONS = [
  { value: "3600", label: "1 hour" },
  { value: "21600", label: "6 hours" },
  { value: "86400", label: "24 hours" },
  { value: "604800", label: "7 days" },
  { value: "2592000", label: "30 days (maximum)" },
];

const LIMIT_OPTIONS = [
  { value: "25", label: "25 records" },
  { value: "50", label: "50 records" },
  { value: "100", label: "100 records (maximum)" },
];

export function validateRunForm(state: RunFormState): string | null {
  if (state.transactionId.trim().length === 0) {
    return "Enter a transaction id. Alerts and Cases link straight into this field.";
  }
  const limit = Number(state.resultLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    return "Result limit must be a whole number between 1 and 100.";
  }
  const hops = Number(state.maxHops);
  if (!Number.isInteger(hops) || hops < 1 || hops > 3) {
    return "Hops must be between 1 and 3; the graph service refuses wider traversals.";
  }
  const window = Number(state.timeWindowSeconds);
  if (!Number.isInteger(window) || window < 1 || window > 2592000) {
    return "Time window must be between 1 second and 30 days.";
  }
  return null;
}

/**
 * Bounded-run form.
 *
 * Bounds are visible because they bound what the investigation can see: a run
 * that returns nothing inside a narrow window has not cleared the transfer.
 */
export function RunForm({
  state,
  onChange,
  onSubmit,
  onReset,
  pending,
  llmConfigured,
  validationError,
  className,
}: {
  state: RunFormState;
  onChange: (patch: Partial<RunFormState>) => void;
  onSubmit: () => void;
  onReset: () => void;
  pending: boolean;
  llmConfigured: boolean;
  validationError: string | null;
  className?: string;
}): React.JSX.Element {
  return (
    <Card className={className}>
      <CardHeader
        title="Bounded investigation"
        description="Runs the approved graph tools over FalkorDB. Only what they return is shown."
        level={2}
      />
      <form
        className="space-y-3.5 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <TextField
          label="Anchor transaction id"
          required
          mono
          value={state.transactionId}
          onChange={(value) => onChange({ transactionId: value })}
          placeholder="hi8-tx-00000001"
          hint="The anchor must exist in the score artifact and in the replay graph."
        />

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SelectField
            label="Hops"
            hideLabel={false}
            value={state.maxHops}
            onChange={(value) => onChange({ maxHops: value })}
            options={HOP_OPTIONS}
            className="flex-col items-stretch gap-1.5"
          />
          <SelectField
            label="Window (UTC)"
            value={state.timeWindowSeconds}
            onChange={(value) => onChange({ timeWindowSeconds: value })}
            options={WINDOW_OPTIONS}
            className="flex-col items-stretch gap-1.5"
          />
          <SelectField
            label="Result limit"
            value={state.resultLimit}
            onChange={(value) => onChange({ resultLimit: value })}
            options={LIMIT_OPTIONS}
            className="flex-col items-stretch gap-1.5"
          />
        </div>

        <div className="space-y-2.5 border-t border-border pt-3">
          <CheckboxField
            label="Use the language model report agent"
            description={
              llmConfigured
                ? "The agent may choose additional approved tool calls and draft the report. Deterministic checks still verify every citation, and any failure falls back."
                : "No provider or key is configured, so the agent cannot run. The deterministic report is used instead."
            }
            checked={state.useLlm}
            onChange={(next) => onChange({ useLlm: next })}
            disabled={!llmConfigured}
            disabledReason="Language model agent unavailable: no provider or key is configured."
          />
          <CheckboxField
            label="Run the controlled case action gate"
            description="May open or enrich a case when a query finds evidence and policy says review. It cannot change a score or touch a payment."
            checked={state.act}
            onChange={(next) => onChange({ act: next })}
          />
        </div>

        {validationError ? (
          <p role="alert" className="text-[12px] text-critical">
            {validationError}
          </p>
        ) : null}

        <div className="flex items-center gap-2 border-t border-border pt-3.5">
          <Button type="submit" variant="primary" icon={Play} loading={pending}>
            {pending ? "Running investigation" : "Run investigation"}
          </Button>
          <Button variant="ghost" icon={RotateCcw} onClick={onReset} disabled={pending}>
            Reset
          </Button>
          <p className="ml-auto text-[11px] text-muted-foreground">
            Bounds are sent as declared. The service does not widen them.
          </p>
        </div>
      </form>
    </Card>
  );
}
