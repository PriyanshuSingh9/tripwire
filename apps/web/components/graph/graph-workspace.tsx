"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { ChevronDown, Network } from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Chip } from "@/components/ui/badge";
import { Disclosure } from "@/components/ui/side-panel";
import { EmptyState } from "@/components/ui/states";
import { EntityPanel } from "@/components/graph/entity-panel";
import {
  GraphCanvas,
  type GraphControls,
  type GraphLayoutName,
  type GraphSelection,
} from "@/components/graph/graph-canvas";
import { GraphLegend, GraphToolbar, GRAPH_MODE_HELP, type GraphMode } from "@/components/graph/graph-toolbar";
import { EvidenceTable, type RowFilter } from "@/components/graph/evidence-table";
import { cn } from "@/lib/utils";
import { usePrefersReducedMotion } from "@/lib/motion";
import type { InvestigationResponse } from "@/lib/api/types";
import {
  accountNodeId,
  egoNetwork,
  elementIdsForPath,
  incidentTransactionIds,
  transactionNodeId,
  type GraphModel,
} from "@/lib/graph/model";

/**
 * Graph workspace: bounded run evidence rendered as a knowledge graph, with a
 * detail rail and the evidence table below.
 *
 * Composition rules held here:
 * - canvas draws only the model projected from the executed query response;
 * - every canvas action has a keyboard-reachable control (toolbar buttons, the
 *   entity list, and the evidence table all select the same records);
 * - the detail panel marks the same record on the canvas and in the table.
 */
export function GraphWorkspace({
  response,
  model,
  selection,
  onSelectionChange,
  mode,
  onModeChange,
  pathIndex,
  onPathIndexChange,
  onExpand,
  expandLabel,
  expandHint,
  expandDisabled,
  rail,
  className,
}: {
  response: InvestigationResponse;
  model: GraphModel;
  selection: GraphSelection | null;
  onSelectionChange: (selection: GraphSelection | null) => void;
  mode: GraphMode;
  onModeChange: (mode: GraphMode) => void;
  pathIndex: number;
  onPathIndexChange: (index: number) => void;
  onExpand?: () => void;
  expandLabel?: string;
  expandHint?: string;
  expandDisabled?: boolean;
  /** Additional right-rail content, rendered below the detail panel. */
  rail?: React.ReactNode;
  className?: string;
}): React.JSX.Element {
  const reducedMotion = usePrefersReducedMotion();
  const controlsRef = useRef<GraphControls | null>(null);
  const [layoutName, setLayoutName] = useState<GraphLayoutName>("flow");
  const [layoutNonce, setLayoutNonce] = useState(0);
  const [graphSearch, setGraphSearch] = useState("");
  const [rowSearch, setRowSearch] = useState("");
  const [rowFilter, setRowFilter] = useState<RowFilter>("all");
  const [entitiesOpen, setEntitiesOpen] = useState(false);

  const needle = graphSearch.trim().toLowerCase();

  const searchKeep = useMemo(() => {
    if (needle.length === 0) return null;
    const keep = new Set<string>();
    for (const node of model.nodes) {
      if (
        node.identifier.toLowerCase().includes(needle) ||
        node.label.toLowerCase().includes(needle)
      ) {
        keep.add(node.id);
      }
    }
    for (const edge of model.edges) {
      if (keep.has(edge.source) || keep.has(edge.target)) {
        keep.add(edge.source);
        keep.add(edge.target);
        keep.add(edge.id);
      }
    }
    return keep;
  }, [model, needle]);

  const selectedNodeId = selection?.kind === "node" ? selection.id : null;

  const keep = useMemo(() => {
    if (mode === "explore" && selectedNodeId) {
      const ego = egoNetwork(model, selectedNodeId);
      if (!searchKeep) return ego;
      const intersection = new Set<string>();
      for (const id of ego) if (searchKeep.has(id)) intersection.add(id);
      return intersection;
    }
    return searchKeep ?? undefined;
  }, [mode, selectedNodeId, model, searchKeep]);

  const highlightIds = useMemo(() => {
    if (mode === "path") {
      if (model.paths.length === 0) return undefined;
      return new Set(elementIdsForPath(model, Math.min(pathIndex, model.paths.length - 1)));
    }
    if (mode === "explore" && selectedNodeId) {
      return new Set(egoNetwork(model, selectedNodeId));
    }
    if (needle.length > 0 && searchKeep) {
      return new Set([...searchKeep]);
    }
    return undefined;
  }, [mode, model, pathIndex, selectedNodeId, needle, searchKeep]);

  const relatedIds = useMemo(() => {
    if (!selection) return undefined;
    if (selection.kind === "edge") {
      const edge = model.edgeById.get(selection.id);
      if (!edge) return undefined;
      return new Set([edge.source, edge.target, edge.id]);
    }
    return new Set(egoNetwork(model, selection.id));
  }, [model, selection]);

  const activeIds = useMemo(() => {
    if (!selection) return new Set<string>();
    if (selection.kind === "edge") {
      const edge = model.edgeById.get(selection.id);
      return new Set(edge ? [edge.transactionId] : []);
    }
    if (selection.kind === "node") {
      const node = model.nodeById.get(selection.id);
      if (!node) return new Set<string>();
      return new Set(incidentTransactionIds(model, selection.id));
    }
    return new Set<string>();
  }, [model, selection]);

  const selectTransaction = useCallback(
    (transactionId: string) => onSelectionChange({ kind: "node", id: transactionNodeId(transactionId) }),
    [onSelectionChange],
  );
  const selectAccount = useCallback(
    (accountKey: string) => onSelectionChange({ kind: "node", id: accountNodeId(accountKey) }),
    [onSelectionChange],
  );

  const dimUnhighlighted =
    (mode === "path" && model.paths.length > 0) || (mode === "explore" && selectedNodeId !== null);

  const matchedCount = searchKeep ? searchKeep.size : model.nodes.length;

  const modeHint = mode === "explore" && !selectedNodeId ? GRAPH_MODE_HELP.explore : null;

  return (
    <div className={cn("grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_336px]", className)}>
      <div className="flex min-w-0 flex-col gap-3">
        <Card className="flex min-h-[440px] flex-col overflow-hidden">
          <CardHeader
            title="Connections"
            description="Accounts and transfers the executed queries returned. Relationships carry the schema's direction."
            level={2}
            action={
              <Chip title="Rendered from the current response only">
                {model.nodes.length} entities · {model.edges.length} relationships
              </Chip>
            }
          />
          <GraphToolbar
            controlsRef={controlsRef}
            mode={mode}
            onModeChange={onModeChange}
            layoutName={layoutName}
            onLayoutChange={setLayoutName}
            search={graphSearch}
            onSearchChange={setGraphSearch}
            matchedCount={matchedCount}
            totalCount={model.nodes.length}
            hasSelection={selection !== null}
            onClearSelection={() => onSelectionChange(null)}
            onExpand={onExpand}
            expandLabel={expandLabel}
            expandHint={expandHint}
            expandDisabled={expandDisabled}
            onRelayout={() => setLayoutNonce((current) => current + 1)}
          />

          {model.nodes.length === 0 ? (
            <EmptyState
              title="Nothing to draw"
              description="The response carries no nodes: the executed queries returned no records inside the declared bounds."
              className="flex-1"
            />
          ) : (
            <GraphCanvas
              model={model}
              keep={keep}
              selection={selection}
              onSelect={onSelectionChange}
              highlightIds={highlightIds}
              dimUnhighlighted={dimUnhighlighted}
              relatedIds={relatedIds}
              layoutName={layoutName}
              layoutNonce={layoutNonce}
              reducedMotion={reducedMotion}
              variant="cards"
              label="Evidence graph of accounts and transfers"
              describedBy="graph-canvas-help"
            />
          )}

          <div className="space-y-2 border-t border-border px-3 py-2.5">
            <GraphLegend variant="cards" />
            <p id="graph-canvas-help" className="text-[11px] leading-relaxed text-muted-foreground">
              Keyboard: Tab reaches the canvas, then arrow keys move the selection, + and - zoom, 0
              fits, Escape clears. Every graph action is also in the toolbar above, and the entity
              list below selects the same records.
            </p>
            {modeHint ? (
              <p className="text-[11px] text-warning">{modeHint}</p>
            ) : null}
          </div>

          <div className="border-t border-border px-3 py-2">
            <Disclosure
              summary={
                <span className="flex items-center gap-1.5">
                  <Network className="size-3.5" aria-hidden="true" />
                  Entities in this result ({model.nodes.length}): keyboard selection
                </span>
              }
              defaultOpen={entitiesOpen}
            >
              <div className="max-h-52 overflow-y-auto pr-1">
                <ul className="space-y-0.5">
                  {model.nodes.map((node) => (
                    <li key={node.id}>
                      <button
                        type="button"
                        onClick={() => onSelectionChange({ kind: "node", id: node.id })}
                        aria-current={selection?.kind === "node" && selection.id === node.id ? "true" : undefined}
                        className={cn(
                          "flex w-full items-center justify-between gap-2 rounded-control px-2 py-1 text-left transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover",
                          selection?.kind === "node" && selection.id === node.id && "bg-surface-active",
                        )}
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-[11.5px] text-foreground">
                            {node.label}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-muted-foreground">
                            {node.typeLabel}
                          </span>
                        </span>
                        {node.onPath ? <Chip mono>path</Chip> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </Disclosure>
          </div>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader
            title="Evidence"
            description="Every record an executed query returned for this run. Selecting a row selects the same element on the canvas."
            level={2}
            action={
              <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <ChevronDown className="size-3.5" aria-hidden="true" />
                {response.evidence.paths.length} paths · {response.evidence.transactions.length} transfers ·{" "}
                {response.evidence.accounts.length} accounts
              </span>
            }
          />
          <EvidenceTable
            model={model}
            activeIds={activeIds}
            onSelectTransaction={selectTransaction}
            onSelectAccount={selectAccount}
            search={rowSearch}
            onSearchChange={setRowSearch}
            filter={rowFilter}
            onFilterChange={setRowFilter}
          />
        </Card>
      </div>

      <div className="min-w-0 space-y-3">
        {selection ? (
          <EntityPanel
            model={model}
            selection={selection}
            onClose={() => onSelectionChange(null)}
            onSelectNode={(nodeId) => onSelectionChange({ kind: "node", id: nodeId })}
            onSelectTransaction={selectTransaction}
            focusLabel="Focus connections on canvas"
            onFocus={() => {
              onModeChange("explore");
              setLayoutNonce((current) => current + 1);
            }}
          />
        ) : (
          <Card>
            <CardHeader
              title="Selection"
              description="Choose an account, transfer or relationship on the canvas, in the entity list, or in the evidence table."
              level={2}
            />
            <div className="px-4 py-4 text-[12px] leading-relaxed text-muted-foreground">
              Nothing is selected. Selections link the canvas, the detail panel and the evidence
              table to the same record.
            </div>
          </Card>
        )}
        {rail}
      </div>
    </div>
  );
}
