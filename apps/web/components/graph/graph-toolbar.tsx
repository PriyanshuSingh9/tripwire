"use client";

import {
  Crosshair,
  Maximize2,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button, IconButton } from "@/components/ui/button";
import { SegmentedControl, SelectField, SearchInput } from "@/components/ui/controls";
import type { GraphControls, GraphLayoutName, GraphNodeVariant } from "@/components/graph/graph-canvas";
import { Chip } from "@/components/ui/badge";

export type GraphMode = "explore" | "path" | "all";

export const GRAPH_MODE_HELP: Record<GraphMode, string> = {
  explore: "Shows the selected entity and its direct connections from this result.",
  path: "Highlights one path the executed queries returned, and dims the rest.",
  all: "Shows every entity and relationship in this result.",
};

/**
 * Graph controls: entity search, display mode, layout, camera and selection.
 * Every control is a real button or input, so each graph function is reachable
 * from the keyboard without touching the canvas.
 */
export function GraphToolbar({
  controlsRef,
  mode,
  onModeChange,
  layoutName,
  onLayoutChange,
  search,
  onSearchChange,
  matchedCount,
  totalCount,
  onClearSelection,
  hasSelection,
  onExpand,
  expandLabel,
  expandHint,
  expandDisabled,
  onRelayout,
}: {
  controlsRef: React.RefObject<GraphControls | null>;
  mode: GraphMode;
  onModeChange: (mode: GraphMode) => void;
  layoutName: GraphLayoutName;
  onLayoutChange: (layout: GraphLayoutName) => void;
  search: string;
  onSearchChange: (value: string) => void;
  matchedCount: number;
  totalCount: number;
  onClearSelection: () => void;
  hasSelection: boolean;
  onExpand?: () => void;
  expandLabel?: string;
  expandHint?: string;
  expandDisabled?: boolean;
  onRelayout?: () => void;
}): React.JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
      <SegmentedControl<GraphMode>
        label="Graph display mode"
        value={mode}
        onChange={onModeChange}
        options={[
          { value: "explore", label: "Explore", hint: GRAPH_MODE_HELP.explore },
          { value: "path", label: "Path", hint: GRAPH_MODE_HELP.path },
          { value: "all", label: "All", hint: GRAPH_MODE_HELP.all },
        ]}
      />

      <SearchInput
        label="Search entities in this result"
        value={search}
        onChange={onSearchChange}
        placeholder="Search accounts or transfers"
        className="w-[215px]"
      />
      {search.trim().length > 0 ? (
        <Chip mono>
          {matchedCount} of {totalCount}
        </Chip>
      ) : null}

      <div className="ml-auto flex flex-wrap items-center gap-1.5">
        <SelectField
          label="Layout"
          hideLabel={false}
          value={layoutName}
          onChange={(next) => onLayoutChange(next as GraphLayoutName)}
          options={[
            { value: "flow", label: "Flow" },
            { value: "force", label: "Force" },
          ]}
          className="mr-1"
        />
        <IconButton
          label="Zoom in"
          icon={ZoomIn}
          onClick={() => controlsRef.current?.zoomIn()}
        />
        <IconButton
          label="Zoom out"
          icon={ZoomOut}
          onClick={() => controlsRef.current?.zoomOut()}
        />
        <IconButton
          label="Fit graph to view"
          icon={Maximize2}
          onClick={() => controlsRef.current?.fit()}
        />
        <IconButton
          label="Reset view and re-run layout"
          icon={RotateCcw}
          onClick={() => {
            controlsRef.current?.reset();
            onRelayout?.();
          }}
        />
        {onExpand ? (
          <Button
            size="sm"
            variant="secondary"
            icon={Crosshair}
            disabled={expandDisabled}
            title={expandHint}
            onClick={onExpand}
          >
            {expandLabel ?? "Expand"}
          </Button>
        ) : null}
        <IconButton
          label="Clear selection"
          icon={X}
          onClick={onClearSelection}
          disabled={!hasSelection}
        />
      </div>
    </div>
  );
}

/** Muted legend: entity kinds and the two relationship directions. */
export function GraphLegend({
  className,
  variant = "dots",
}: {
  className?: string;
  variant?: GraphNodeVariant;
}): React.JSX.Element {
  const cards = variant === "cards";
  return (
    <div className={className}>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={
              cards
                ? "inline-block h-2.5 w-4 rounded-[3px] border border-[rgba(230,231,229,0.3)] bg-[#1c1e1d]"
                : "inline-block size-3 rounded-full border border-[rgba(230,231,229,0.3)] bg-[#1f2120]"
            }
          />
          Account
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={
              cards
                ? "inline-block h-2 w-3.5 rounded-[3px] border border-[rgba(230,231,229,0.2)] bg-[#232628]"
                : "inline-block size-2.5 rotate-45 border border-[rgba(230,231,229,0.2)] bg-[#26282a]"
            }
          />
          Transaction
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-px w-5 bg-[rgba(230,231,229,0.35)]" />
          sent / received by
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-0.5 w-5 bg-primary" />
          on a returned path or selection
        </li>
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={
              cards
                ? "inline-block h-2.5 w-4 rounded-[3px] border-2 border-primary bg-[#2a2118]"
                : "inline-block size-3 rounded-full border-2 border-primary bg-[#2a2118]"
            }
          />
          anchor transfer endpoints
        </li>
      </ul>
    </div>
  );
}
