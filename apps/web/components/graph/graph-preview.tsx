"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { GraphCanvas, type GraphSelection } from "@/components/graph/graph-canvas";
import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/states";
import { DecisionBadge, OutcomeBadge } from "@/components/ui/semantic";
import { usePrefersReducedMotion } from "@/lib/motion";
import { formatScore, formatTimestamp } from "@/lib/format";
import type { InvestigationResponse } from "@/lib/api/types";
import { buildGraphModel, previewSubset } from "@/lib/graph/model";

/**
 * Compact evidence preview.
 *
 * Renders a bounded subset of one real investigation response: the anchor
 * neighbourhood and the entities on returned paths. It is a preview, not an
 * expansion of the search, and it says so.
 */
export function GraphPreview({
  response,
  maxNodes = 14,
  className,
}: {
  response: InvestigationResponse;
  maxNodes?: number;
  className?: string;
}): React.JSX.Element {
  const reducedMotion = usePrefersReducedMotion();
  const [selection, setSelection] = useState<GraphSelection | null>(null);
  const model = useMemo(() => buildGraphModel(response), [response]);
  const keep = useMemo(() => previewSubset(model, { maxNodes }), [model, maxNodes]);

  const selected = selection?.kind === "node" ? model.nodeById.get(selection.id) : undefined;
  const selectedEdge = selection?.kind === "edge" ? model.edgeById.get(selection.id) : undefined;

  if (model.nodes.length === 0) {
    return (
      <EmptyState
        className={className}
        title="No graph to preview"
        description="This run returned no accounts or transfers inside its bounds, so there is nothing to draw."
      />
    );
  }

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <DecisionBadge decision={response.decision} />
        <OutcomeBadge outcome={response.outcome} />
        <StatusBadge label={`${formatScore(response.risk_score)} risk`} tone="neutral" />
        <span className="text-[11px] text-muted-foreground">
          {response.transaction_id} · {formatTimestamp(response.created_at)}
        </span>
      </div>

      <GraphCanvas
        model={model}
        keep={keep}
        selection={selection}
        onSelect={setSelection}
        relatedIds={
          selection
            ? new Set([
                ...(selection.kind === "edge" ? [selection.id] : []),
                ...model.edges
                  .filter(
                    (edge) =>
                      edge.source === selection.id ||
                      edge.target === selection.id ||
                      edge.id === selection.id,
                  )
                  .map((edge) => edge.id),
              ])
            : undefined
        }
        reducedMotion={reducedMotion}
        layoutName="flow"
        compact
        className="rounded-control border border-border"
        label="Preview of the evidence graph for this investigation"
        describedBy="graph-preview-help"
      />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <p id="graph-preview-help" className="text-[11px] text-muted-foreground">
          {selected
            ? `${selected.typeLabel}: ${selected.identifier}`
            : selectedEdge
              ? `${selectedEdge.kind === "SENT" ? "SENT" : "RECEIVED"} · transfer ${selectedEdge.transactionId}`
              : `Preview of up to ${maxNodes} entities from this result. Select an entity for its identifier.`}
        </p>
        <Link
          href={`/simulation?transaction_id=${encodeURIComponent(response.transaction_id)}`}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
        >
          Open full workspace
          <ArrowUpRight className="size-3" aria-hidden="true" />
        </Link>
      </div>
    </div>
  );
}
