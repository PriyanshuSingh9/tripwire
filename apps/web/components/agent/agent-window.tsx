"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronsRight,
  Network,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
} from "lucide-react";

import { Card, CardHeader } from "@/components/ui/card";
import { Button, IconButton } from "@/components/ui/button";
import { Chip } from "@/components/ui/badge";
import { Disclosure } from "@/components/ui/side-panel";
import { EmptyState } from "@/components/ui/states";
import { AgentConsole } from "@/components/agent/agent-console";
import { EscalationCard } from "@/components/agent/escalation-card";
import { OpenCaseCard } from "@/components/cases/open-case-card";
import { EntityPanel } from "@/components/graph/entity-panel";
import {
  GraphCanvas,
  type GraphControls,
  type GraphLayoutName,
  type GraphSelection,
} from "@/components/graph/graph-canvas";
import {
  GraphLegend,
  GraphToolbar,
  GRAPH_MODE_HELP,
  type GraphMode,
} from "@/components/graph/graph-toolbar";
import { EvidenceTable, type RowFilter } from "@/components/graph/evidence-table";
import { RiskPanel } from "@/components/graph/risk-panel";
import { cn } from "@/lib/utils";
import { usePrefersReducedMotion } from "@/lib/motion";
import type { InvestigationRequest, InvestigationResponse } from "@/lib/api/types";
import {
  accountNodeId,
  egoNetwork,
  elementIdsForPath,
  incidentTransactionIds,
  transactionNodeId,
  type GraphModel,
} from "@/lib/graph/model";
import { buildReplaySteps, stepCaption } from "@/lib/graph/replay";

const REPLAY_INTERVAL_MS = 1050;

/**
 * The agent window: the recorded investigation as a chat-style activity
 * timeline beside the evidence graph that grows with it.
 *
 * The service returns one completed response, so the growth is a replay of
 * that response in recorded order, and the window says so. Every drawn record
 * comes from the response; the replay only controls when it appears.
 */
export function AgentWindow({
  response,
  request,
  model,
  selection,
  onSelectionChange,
  mode,
  onModeChange,
  pathIndex,
  onPathIndexChange,
  onWiden,
  widenDisabled,
  className,
}: {
  response: InvestigationResponse;
  request: InvestigationRequest;
  model: GraphModel;
  selection: GraphSelection | null;
  onSelectionChange: (selection: GraphSelection | null) => void;
  mode: GraphMode;
  onModeChange: (mode: GraphMode) => void;
  pathIndex: number;
  onPathIndexChange: (index: number) => void;
  onWiden?: () => void;
  widenDisabled?: boolean;
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

  const steps = useMemo(() => buildReplaySteps(response, model), [response, model]);
  const lastIndex = Math.max(steps.length - 1, 0);

  // The window is keyed by the investigation id, so a new response remounts
  // this component and these initialisers run again: the replay starts at the
  // first step, or opens at the final state under reduced motion.
  const [cursor, setCursor] = useState(() => (reducedMotion ? lastIndex : 0));
  const [playing, setPlaying] = useState(() => !reducedMotion);

  const safeCursor = Math.min(Math.max(cursor, 0), lastIndex);
  const activeStep = steps[safeCursor] ?? null;
  const atEnd = safeCursor >= lastIndex;

  // Auto-advance while playing. The timer is keyed to the cursor, so each
  // advance schedules the next; sitting on the final step stops the timer on
  // its own because `atEnd` turns true. The control labels read from `atEnd`
  // rather than mutating state.
  useEffect(() => {
    if (!playing || atEnd) return;
    const next = Math.min(cursor + 1, lastIndex);
    const timer = window.setTimeout(() => {
      setCursor(next);
    }, REPLAY_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [playing, atEnd, lastIndex, cursor]);

  const revealedIds = useMemo(() => {
    const ids = new Set<string>();
    for (let index = 0; index <= safeCursor && index < steps.length; index += 1) {
      const step = steps[index];
      if (!step) continue;
      for (const id of step.addIds) ids.add(id);
    }
    return ids;
  }, [steps, safeCursor]);

  const drawnNodes = useMemo(
    () => model.nodes.filter((node) => revealedIds.has(node.id)).length,
    [model, revealedIds],
  );

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
    const baseKeep = (() => {
      if (mode === "explore" && selectedNodeId) {
        const ego = egoNetwork(model, selectedNodeId);
        if (!searchKeep) return ego;
        const intersection = new Set<string>();
        for (const id of ego) if (searchKeep.has(id)) intersection.add(id);
        return intersection;
      }
      return searchKeep;
    })();
    if (baseKeep === null) return revealedIds;
    const out = new Set<string>();
    for (const id of baseKeep) if (revealedIds.has(id)) out.add(id);
    return out;
  }, [mode, selectedNodeId, model, searchKeep, revealedIds]);

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

  // The replay step's records glow while its step is the cursor; the final
  // step settles into the plain evidence state.
  const stepRelated = useMemo(() => {
    if (safeCursor >= lastIndex || !activeStep || activeStep.highlightIds.length === 0) {
      return null;
    }
    return new Set(activeStep.highlightIds);
  }, [safeCursor, lastIndex, activeStep]);

  const relatedIds = useMemo(() => {
    if (!selection && !stepRelated) return undefined;
    const out = new Set<string>();
    if (stepRelated) for (const id of stepRelated) out.add(id);
    if (selection) {
      if (selection.kind === "edge") {
        const edge = model.edgeById.get(selection.id);
        if (edge) {
          out.add(edge.source);
          out.add(edge.target);
          out.add(edge.id);
        }
      } else {
        for (const id of egoNetwork(model, selection.id)) out.add(id);
      }
    }
    return out;
  }, [model, selection, stepRelated]);

  const activeIds = useMemo(() => {
    if (!selection) return new Set<string>();
    if (selection.kind === "edge") {
      const edge = model.edgeById.get(selection.id);
      return new Set(edge ? [edge.transactionId] : []);
    }
    if (selection.kind === "node") {
      if (!model.nodeById.has(selection.id)) return new Set<string>();
      return new Set(incidentTransactionIds(model, selection.id));
    }
    return new Set<string>();
  }, [model, selection]);

  const selectTransaction = useCallback(
    (transactionId: string) =>
      onSelectionChange({ kind: "node", id: transactionNodeId(transactionId) }),
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

  const togglePlay = useCallback(() => {
    if (playing && !atEnd) {
      setPlaying(false);
      return;
    }
    if (atEnd) setCursor(0);
    setPlaying(true);
  }, [playing, atEnd]);

  const caption = stepCaption(activeStep);

  return (
    <div className={cn("space-y-3", className)}>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <AgentConsole
          response={response}
          request={request}
          steps={steps}
          cursor={safeCursor}
          onCursorChange={(index) => {
            setPlaying(false);
            setCursor(index);
          }}
          className="xl:h-full"
        />

        <div className="min-w-0 space-y-3">
          <Card className="flex min-h-[580px] flex-col overflow-hidden">
            <CardHeader
              title="Investigation graph"
              description="Accounts and transfers the executed queries returned, drawn as each recorded call runs. Relationships follow the schema direction."
              level={2}
              action={
                <Chip title="Entities drawn so far against the full response">
                  {drawnNodes} of {model.nodes.length} entities drawn
                </Chip>
              }
            />

            <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
              <IconButton
                label={playing && !atEnd ? "Pause the replay" : "Play the replay"}
                icon={playing && !atEnd ? Pause : Play}
                onClick={togglePlay}
              />
              <IconButton
                label="Step back one recorded step"
                icon={SkipBack}
                onClick={() => {
                  setPlaying(false);
                  setCursor((current) => Math.max(current - 1, 0));
                }}
                disabled={safeCursor === 0}
              />
              <IconButton
                label="Step forward one recorded step"
                icon={SkipForward}
                onClick={() => {
                  setPlaying(false);
                  setCursor((current) => Math.min(current + 1, lastIndex));
                }}
                disabled={safeCursor >= lastIndex}
              />
              <IconButton
                label="Restart the replay"
                icon={RotateCcw}
                onClick={() => {
                  setCursor(0);
                  setPlaying(!reducedMotion);
                }}
              />
              <Button
                size="sm"
                variant="secondary"
                icon={ChevronsRight}
                onClick={() => {
                  setPlaying(false);
                  setCursor(lastIndex);
                }}
                disabled={safeCursor >= lastIndex}
              >
                Skip to end
              </Button>
              <span
                role="status"
                aria-live={playing && !atEnd ? "off" : "polite"}
                className="min-w-0 flex-1 truncate text-right text-[11px] text-muted-foreground"
                title={caption}
              >
                Step {safeCursor + 1} of {steps.length} · {caption}
              </span>
            </div>

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
              onExpand={onWiden}
              expandLabel="Widen bounds"
              expandHint="Runs a new investigation with one more hop and a larger record limit. It does not expand the current result."
              expandDisabled={widenDisabled}
              onRelayout={() => setLayoutNonce((current) => current + 1)}
            />

            {model.nodes.length === 0 ? (
              <EmptyState
                title="Nothing to draw"
                description="The response carries no nodes: the executed queries returned no records inside the declared bounds."
                className="flex-1"
              />
            ) : (
              <div className="relative flex min-h-[440px] flex-1 flex-col">
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
                  label="Investigation graph growing with the recorded tool calls"
                  describedBy="agent-graph-help"
                />
                {drawnNodes === 0 ? (
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <p className="rounded-control border border-border bg-[#0a0b0b]/85 px-3 py-2 text-[11.5px] text-muted-foreground">
                      Nothing drawn yet. Play or step the replay to watch the graph grow.
                    </p>
                  </div>
                ) : null}
              </div>
            )}

            <div className="space-y-2 border-t border-border px-3 py-2.5">
              <GraphLegend variant="cards" />
              <p id="agent-graph-help" className="text-[11px] leading-relaxed text-muted-foreground">
                The replay walks the recorded tool calls in order and draws the records the response
                attributes to each one. Records the attribution cannot pin to a single call arrive
                at the evidence step. Keyboard: Tab reaches the canvas, then arrow keys move the
                selection, + and - zoom, 0 fits, Escape clears.
              </p>
              {reducedMotion ? (
                <p className="text-[11px] text-muted-foreground">
                  Reduced motion is on, so the replay opens at the final state. Use the step buttons
                  to walk it manually.
                </p>
              ) : null}
              {modeHint ? <p className="text-[11px] text-warning">{modeHint}</p> : null}
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
                          aria-current={
                            selection?.kind === "node" && selection.id === node.id
                              ? "true"
                              : undefined
                          }
                          className={cn(
                            "flex w-full items-center justify-between gap-2 rounded-control px-2 py-1 text-left transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover",
                            selection?.kind === "node" && selection.id === node.id && "bg-surface-active",
                            !revealedIds.has(node.id) && "opacity-60",
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
                          <span className="flex shrink-0 items-center gap-1.5">
                            {!revealedIds.has(node.id) ? <Chip>not drawn yet</Chip> : null}
                            {node.onPath ? <Chip mono>path</Chip> : null}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </Disclosure>
            </div>
          </Card>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <EscalationCard response={response} />
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
          </div>

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

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <RiskPanel
          response={response}
          model={model}
          onSelectPath={(index) => {
            onPathIndexChange(index);
            onModeChange("path");
          }}
          onInspectTransaction={(transactionId) =>
            onSelectionChange({ kind: "node", id: transactionNodeId(transactionId) })
          }
        />
        <OpenCaseCard
          transactionId={response.transaction_id}
          caseId={response.action?.case_id ?? null}
        />
      </div>
    </div>
  );
}
