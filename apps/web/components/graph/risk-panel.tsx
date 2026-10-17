"use client";

import { FileText, Route, ShieldAlert } from "lucide-react";

import { Chip, StatusBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, StatLine } from "@/components/ui/card";
import { Disclosure } from "@/components/ui/side-panel";
import { EmptyState } from "@/components/ui/states";
import { DecisionBadge, OutcomeBadge } from "@/components/ui/semantic";
import { formatScore, formatTimestamp } from "@/lib/format";
import type { InvestigationResponse } from "@/lib/api/types";
import { elementIdsForPath, type GraphModel } from "@/lib/graph/model";

/**
 * Risk, policy and report panel.
 *
 * The score is presented as a model estimate, the decision as deterministic
 * policy, and the report as claims that cite returned records. Nothing here
 * reads like a verdict.
 */
export function RiskPanel({
  response,
  model,
  onSelectPath,
  onInspectTransaction,
}: {
  response: InvestigationResponse;
  model: GraphModel;
  onSelectPath: (pathIndex: number) => void;
  onInspectTransaction: (transactionId: string) => void;
}): React.JSX.Element {
  const report = response.report ?? null;
  const llm = report?.llm ?? null;

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="Risk and policy"
          description="Model estimate mapped to a decision by deterministic code."
          level={2}
        />
        <CardBody className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 pb-2">
            <span className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
              Risk score
            </span>
            <span className="tabular text-[24px] leading-none font-semibold text-foreground">
              {formatScore(response.risk_score)}
            </span>
          </div>
          <StatLine
            label="Decision"
            value={<DecisionBadge decision={response.decision} />}
          />
          <StatLine label="Outcome" value={<OutcomeBadge outcome={response.outcome} />} />
          <StatLine label="Policy version" value={<span className="font-mono text-[11px]">{response.policy_version}</span>} />
          <StatLine label="Anchor transaction" value={<span className="font-mono text-[11px]">{response.transaction_id}</span>} />
          <StatLine label="Investigation id" value={<span className="font-mono text-[11px]">{response.investigation_id}</span>} />
          <StatLine label="Created" value={formatTimestamp(response.created_at)} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Report"
          description={
            report === null
              ? "No report accompanies this response."
              : `Citation-checked before display. Source: ${report.source}.`
          }
          level={2}
          action={
            report ? (
              <StatusBadge
                label={report.validation === "validated" ? "Validated" : report.validation}
                tone="safe"
                icon={FileText}
              />
            ) : null
          }
        />
        {report === null ? (
          <EmptyState
            title="No report for this run"
            description="The response carries no report block, so there is nothing to cite. The evidence above is still from executed queries."
          />
        ) : (
          <CardBody className="space-y-3">
            <div>
              <p className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Working hypothesis
              </p>
              <p className="mt-1 text-[12.5px] leading-relaxed text-foreground">
                {report.hypothesis}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Chip>recommendation: {report.recommendation}</Chip>
              <Chip>{report.source === "llm" ? "Language model draft, verified" : "Deterministic"}</Chip>
              {llm ? (
                <Chip title={`Provider ${llm.provider} · ${llm.model}`}>
                  {llm.status === "completed"
                    ? "provider completed"
                    : llm.status === "fallback"
                      ? `fallback: ${llm.fallback_reason}`
                      : "provider unavailable"}
                </Chip>
              ) : null}
            </div>

            <section>
              <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                Cited transfers ({report.cited_claims.length})
              </h3>
              {report.cited_claims.length === 0 ? (
                <p className="mt-1 text-[12px] text-muted-foreground">
                  The report cites no individual transfer.
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1">
                  {report.cited_claims.map((claim) => (
                    <li key={claim.transaction_id}>
                      <button
                        type="button"
                        onClick={() => onInspectTransaction(claim.transaction_id)}
                        className="w-full rounded-control border border-border bg-muted px-2.5 py-1.5 text-left transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
                      >
                        <span className="block truncate font-mono text-[11px] text-foreground">
                          {claim.transaction_id}
                        </span>
                        <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                          {claim.source_account_key ?? "unknown source"} → {claim.destination_account_key ?? "unknown destination"}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {report.counterevidence_ids.length > 0 ? (
              <section>
                <h3 className="text-[11px] tracking-[0.03em] text-muted-foreground uppercase">
                  Counterevidence ({report.counterevidence_ids.length})
                </h3>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                  Records the report treats as pointing away from the hypothesis. They stay
                  visible so the case is not read as one-sided.
                </p>
                <ul className="mt-1.5 flex flex-wrap gap-1.5">
                  {report.counterevidence_ids.map((id) => (
                    <li key={id}>
                      <Chip mono>{id}</Chip>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <Disclosure summary="Report reference sets">
              <dl className="space-y-1">
                <StatLine
                  label="supporting_transaction_ids"
                  value={String(report.supporting_transaction_ids.length)}
                  tone="muted"
                />
                <StatLine
                  label="cited_paths"
                  value={String(report.cited_paths.length)}
                  tone="muted"
                />
                {llm ? (
                  <>
                    <StatLine label="llm.status" value={llm.status} tone="muted" />
                    <StatLine label="llm.rounds" value={String(llm.rounds)} tone="muted" />
                    <StatLine label="llm.tool_calls" value={String(llm.tool_calls)} tone="muted" />
                    <StatLine
                      label="llm.duration_ms"
                      value={String(Math.round(llm.duration_ms))}
                      tone="muted"
                    />
                  </>
                ) : null}
              </dl>
            </Disclosure>
          </CardBody>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Returned paths"
          description="Ordered chains the executed queries returned. Select one to highlight it on the canvas."
          level={2}
          action={
            <StatusBadge
              label={`${model.paths.length} ${model.paths.length === 1 ? "path" : "paths"}`}
              tone={model.paths.length > 0 ? "warning" : "neutral"}
              icon={Route}
            />
          }
        />
        {model.paths.length === 0 ? (
          <EmptyState
            title="No path was returned"
            description={
              response.outcome === "no_matching_path"
                ? "The executed bounds returned no chain. This is a bounded search result, not a clearance."
                : "Path queries matched nothing inside the declared bounds."
            }
          />
        ) : (
          <CardBody className="space-y-1.5">
            {model.paths.map((path, index) => (
              <button
                key={`${path.anchor_transaction_id}-${path.direction}-${index}`}
                type="button"
                onClick={() => onSelectPath(index)}
                className="w-full rounded-control border border-border bg-muted px-2.5 py-2 text-left transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-[12px] font-medium text-foreground">
                    Path {index + 1}
                  </span>
                  <Chip mono>
                    {path.direction} · depth {path.depth}
                  </Chip>
                </span>
                <span className="mt-1 block truncate font-mono text-[10.5px] text-muted-foreground">
                  {path.account_keys.join(" → ")}
                </span>
                <span className="mt-0.5 block text-[10.5px] text-muted-foreground">
                  {path.transaction_ids.length} transfer
                  {path.transaction_ids.length === 1 ? "" : "s"} ·{" "}
                  {elementIdsForPath(model, index).length} graph elements
                </span>
              </button>
            ))}
          </CardBody>
        )}
      </Card>

      {response.action ? (
        <Card>
          <CardHeader
            title="Controlled action"
            description="The deterministic gate decides whether a proposed case action runs."
            level={2}
            action={
              <StatusBadge
                label={response.action.status}
                tone={
                  response.action.status === "executed"
                    ? "safe"
                    : response.action.status === "denied"
                      ? "warning"
                      : "neutral"
                }
                icon={ShieldAlert}
              />
            }
          />
          <CardBody className="space-y-1">
            <StatLine label="Proposed" value={response.action.proposed_action} />
            <StatLine label="Proposed by" value={response.action.proposed_by} />
            <StatLine label="Reason" value={response.action.reason} />
            {response.action.denial_code ? (
              <StatLine
                label="Denial code"
                value={response.action.denial_code}
                tone="warning"
              />
            ) : null}
            {response.action.case_id ? (
              <StatLine label="Case id" value={response.action.case_id} />
            ) : null}
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
