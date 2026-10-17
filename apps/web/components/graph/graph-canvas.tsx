"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from "cytoscape";

import { cn } from "@/lib/utils";
import { toCytoscapeElements, type GraphEdge, type GraphModel, type GraphNode } from "@/lib/graph/model";

export type GraphLayoutName = "flow" | "force";

/**
 * Node rendering style. `dots` is the compact form used by the small previews;
 * `cards` is the labelled entity-card form used by the workspace canvases,
 * where an account reads as its number and a transfer as its amount.
 */
export type GraphNodeVariant = "dots" | "cards";

export interface GraphSelection {
  kind: "node" | "edge";
  id: string;
}

const DOTS_STYLESHEET: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      "background-color": "#1f2120",
      "border-width": 1,
      "border-color": "rgba(230,231,229,0.22)",
      label: "data(label)",
      color: "#c9cbc7",
      "font-size": 8,
      "font-family": "Inter, system-ui, sans-serif",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "text-max-width": "92px",
      "text-wrap": "ellipsis",
      "text-background-color": "#080909",
      "text-background-opacity": 0.72,
      "text-background-padding": "1px",
      width: 22,
      height: 22,
      "transition-property": "background-color, border-color, opacity",
      "transition-duration": 180,
    },
  },
  {
    selector: "node[kind = 'transaction']",
    style: {
      shape: "round-diamond",
      width: 14,
      height: 14,
      "background-color": "#26282a",
      "border-color": "rgba(230,231,229,0.16)",
    },
  },
  {
    selector: "node[isAnchor]",
    style: {
      "border-width": 2,
      "border-color": "#f28c38",
    },
  },
  {
    selector: "node.on-path",
    style: {
      "border-color": "#f28c38",
      "background-color": "#2a2118",
    },
  },
  {
    selector: "node.selected",
    style: {
      "border-width": 3,
      "border-color": "#f28c38",
      "background-color": "#3a2a17",
      color: "#e6e7e5",
      "font-size": 9,
      "z-index": 20,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "rgba(230,231,229,0.20)",
      "target-arrow-color": "rgba(230,231,229,0.20)",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.7,
      "curve-style": "bezier",
      label: "data(label)",
      "font-size": 7,
      "font-family": "Inter, system-ui, sans-serif",
      color: "#858985",
      "text-rotation": "none",
      "text-background-color": "#080909",
      "text-background-opacity": 0.85,
      "text-background-padding": "1px",
      "transition-property": "line-color, opacity",
      "transition-duration": 180,
    },
  },
  {
    selector: "edge.on-path",
    style: {
      width: 1.6,
      "line-color": "#f28c38",
      "target-arrow-color": "#f28c38",
    },
  },
  {
    selector: "edge.selected",
    style: {
      width: 2.4,
      "line-color": "#f28c38",
      "target-arrow-color": "#f28c38",
      "z-index": 20,
    },
  },
  {
    selector: ".dimmed",
    style: {
      opacity: 0.18,
    },
  },
  {
    selector: "node.related",
    style: {
      "border-color": "rgba(242,140,56,0.55)",
    },
  },
  {
    selector: ".fresh",
    style: {
      opacity: 0,
    },
  },
];

/**
 * Labelled entity cards: an account is a wide card carrying its number and
 * bank, a transfer a smaller card carrying its amount and currency. The
 * neutral surfaces stay quiet so the accent keeps meaning anchor, path and
 * selection. Relationship text is left out here; the arrows and the legend
 * carry direction, and panels carry the identifiers.
 */
const CARDS_STYLESHEET: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      shape: "round-rectangle",
      width: 104,
      height: 40,
      "background-color": "#1c1e1d",
      "border-width": 1,
      "border-color": "rgba(230,231,229,0.20)",
      label: "data(cardLabel)",
      color: "#c6c8c4",
      "font-size": 8,
      "font-family": "Inter, system-ui, sans-serif",
      "text-valign": "center",
      "text-halign": "center",
      "text-wrap": "wrap",
      "text-max-width": "88px",
      "transition-property": "background-color, border-color, opacity",
      "transition-duration": 180,
    },
  },
  {
    selector: "node[kind = 'transaction']",
    style: {
      width: 92,
      height: 36,
      "background-color": "#232628",
      "border-color": "rgba(230,231,229,0.16)",
      color: "#b9bcb8",
    },
  },
  {
    selector: "node[isAnchor]",
    style: {
      "border-width": 2,
      "border-color": "#f28c38",
      "background-color": "#2a2118",
      color: "#e6e7e5",
    },
  },
  {
    selector: "node.on-path",
    style: {
      "border-color": "rgba(242,140,56,0.45)",
    },
  },
  {
    selector: "node.selected",
    style: {
      "border-width": 2,
      "border-color": "#f28c38",
      "background-color": "#3a2a17",
      color: "#f2f3f0",
      "z-index": 20,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "rgba(230,231,229,0.18)",
      "target-arrow-color": "rgba(230,231,229,0.24)",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.6,
      "curve-style": "bezier",
      "transition-property": "line-color, opacity",
      "transition-duration": 180,
    },
  },
  {
    selector: "edge.on-path",
    style: {
      width: 1.6,
      "line-color": "#f28c38",
      "target-arrow-color": "#f28c38",
    },
  },
  {
    selector: "edge.selected",
    style: {
      width: 2.4,
      "line-color": "#f28c38",
      "target-arrow-color": "#f28c38",
      "z-index": 20,
    },
  },
  {
    selector: ".dimmed",
    style: {
      opacity: 0.18,
    },
  },
  {
    selector: "node.related",
    style: {
      "border-width": 2,
      "border-color": "rgba(242,140,56,0.7)",
    },
  },
  {
    selector: ".fresh",
    style: {
      opacity: 0,
    },
  },
];

function stylesheetFor(variant: GraphNodeVariant): cytoscape.StylesheetJson {
  return variant === "cards" ? CARDS_STYLESHEET : DOTS_STYLESHEET;
}

function layoutFor(
  name: GraphLayoutName,
  anchorId: string | null,
  reducedMotion: boolean,
  variant: GraphNodeVariant,
): LayoutOptions {
  if (variant === "cards") {
    // Workspace graphs in this dataset are full of account self-loops and wide
    // fans. A layered row layout degenerates into one endless line, so the
    // card canvases use a force layout that keeps the clusters readable:
    // Flow is the compact solve, Force spreads it out.
    const wide = name === "force";
    return {
      name: "cose",
      animate: !reducedMotion,
      animationDuration: 320,
      padding: 30,
      randomize: false,
      nodeRepulsion: () => (wide ? 30000 : 14000),
      idealEdgeLength: () => (wide ? 140 : 84),
      edgeElasticity: () => (wide ? 60 : 90),
      gravity: wide ? 0.5 : 0.9,
      nodeOverlap: 16,
      componentSpacing: 60,
      numIter: 900,
      fit: true,
    };
  }
  if (name === "force") {
    return {
      name: "cose",
      animate: !reducedMotion,
      animationDuration: 320,
      padding: 24,
      nodeRepulsion: () => 12000,
      idealEdgeLength: () => 64,
      edgeElasticity: () => 90,
      nodeOverlap: 14,
      numIter: 900,
      randomize: true,
      componentSpacing: 40,
      fit: true,
    };
  }
  return {
    name: "breadthfirst",
    animate: !reducedMotion,
    animationDuration: 260,
    directed: true,
    padding: 22,
    spacingFactor: 1.15,
    avoidOverlap: true,
    grid: true,
    roots: anchorId ? [anchorId] : undefined,
    fit: true,
  };
}

export interface GraphCanvasProps {
  model: GraphModel;
  /** Entity/edge ids to render. Omit to render the whole result. */
  keep?: Set<string>;
  selection: GraphSelection | null;
  onSelect: (selection: GraphSelection | null) => void;
  /** Ids that belong to the highlighted path or evidence reference set. */
  highlightIds?: Set<string>;
  /** Dim everything outside `highlightIds`. */
  dimUnhighlighted?: boolean;
  layoutName?: GraphLayoutName;
  layoutNonce?: number;
  reducedMotion: boolean;
  className?: string;
  label: string;
  describedBy?: string;
  compact?: boolean;
  /** Node rendering style; workspace canvases use labelled cards. */
  variant?: GraphNodeVariant;
  /** Extra ids to mark as related to the current selection or replay step. */
  relatedIds?: Set<string>;
  /** Camera controls for the surrounding toolbar. */
  controlsRef?: React.RefObject<GraphControls | null>;
}

const elementId = (definition: ElementDefinition): string => String(definition.data.id);

const isEdgeDefinition = (definition: ElementDefinition): boolean =>
  typeof (definition.data as Record<string, unknown>).source === "string" &&
  typeof (definition.data as Record<string, unknown>).target === "string";

/**
 * Cytoscape wrapper.
 *
 * The renderer owns graph layout and graph-element interaction. Everything
 * around it (toolbar, panels, tables) uses the shared UI motion system. The
 * canvas never invents structure: it draws the model projected from the
 * executed query response.
 *
 * Element changes are applied as a diff against what is already on the canvas,
 * so a growing element set (the replay) keeps existing nodes in place and
 * animates new nodes into the layout instead of rebuilding the scene.
 */
export function GraphCanvas({
  model,
  keep,
  selection,
  onSelect,
  highlightIds,
  dimUnhighlighted = false,
  layoutName = "flow",
  layoutNonce = 0,
  reducedMotion,
  className,
  label,
  describedBy,
  compact = false,
  variant = "dots",
  relatedIds,
  controlsRef,
}: GraphCanvasProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const modelRef = useRef(model);
  const onSelectRef = useRef(onSelect);
  const fadeFrameRef = useRef<number | null>(null);
  const appliedLayoutRef = useRef<string | null>(layoutName);
  const [zoom, setZoom] = useState(1);

  // Event handlers read the current model and callback through refs, so the
  // renderer is mounted once. The refs are synced in effects rather than during
  // render.
  useEffect(() => {
    modelRef.current = model;
  }, [model]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  const elements = useMemo<ElementDefinition[]>(() => {
    const payload = toCytoscapeElements(model, { keep });
    return [
      ...payload.nodes.map((node) => ({ data: node.data })),
      ...payload.edges.map((edge) => ({ data: edge.data })),
    ];
  }, [model, keep]);

  const anchorId = useMemo(() => {
    const anchorTransaction = model.nodes.find(
      (node) => node.isAnchor && node.kind === "transaction",
    );
    if (anchorTransaction) return anchorTransaction.id;
    const anchorAccount = model.nodes.find((node) => node.isAnchor && node.kind === "account");
    if (anchorAccount) return anchorAccount.id;
    return model.nodes.find((node) => node.kind === "account")?.id ?? null;
  }, [model]);

  // Mount the renderer once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: stylesheetFor(variant),
      elements: [],
      wheelSensitivity: 0.18,
      minZoom: 0.15,
      maxZoom: 2,
      boxSelectionEnabled: false,
      selectionType: "single",
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event) => {
      const id = event.target.id() as string;
      onSelectRef.current({ kind: "node", id });
    });
    cy.on("tap", "edge", (event) => {
      const id = event.target.id() as string;
      onSelectRef.current({ kind: "edge", id });
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelectRef.current(null);
    });
    const handleZoom = (): void => setZoom(cy.zoom());
    cy.on("zoom", handleZoom);

    return () => {
      cy.off("zoom", handleZoom);
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A variant change swaps the stylesheet without touching the element set.
  useEffect(() => {
    cyRef.current?.style(stylesheetFor(variant));
  }, [variant]);

  // Element set changes: diff against the canvas, grow smoothly, re-layout.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const target = new Map<string, ElementDefinition>();
    for (const definition of elements) target.set(elementId(definition), definition);

    const stale = cy.elements().filter((element) => !target.has(element.id()));
    const removedCount = stale.length;
    if (removedCount > 0) stale.remove();

    const additions = elements.filter((definition) => cy.getElementById(elementId(definition)).empty());
    const addedNodes = additions.filter((definition) => !isEdgeDefinition(definition));
    const addedEdges = additions.filter((definition) => isEdgeDefinition(definition));

    if (additions.length > 0) {
      // Pre-position new nodes near a neighbour already on the canvas (or at
      // the current centre) so the layout animates them into place instead of
      // flying in from the origin.
      const visible = new Set<string>(cy.nodes().map((node) => node.id()));
      const centre = (() => {
        const nodes = cy.nodes();
        if (nodes.length === 0) return { x: cy.width() / 2, y: cy.height() / 2 };
        let x = 0;
        let y = 0;
        nodes.forEach((node) => {
          x += node.position("x");
          y += node.position("y");
        });
        return { x: x / nodes.length, y: y / nodes.length };
      })();

      let orphanIndex = 0;

      cy.batch(() => {
        addedNodes.forEach((definition, index) => {
          const id = elementId(definition);
          cy.add(definition);
          const neighbourId = elements
            .filter((candidate) => {
              if (!isEdgeDefinition(candidate)) return false;
              const source = String((candidate.data as Record<string, unknown>).source);
              const target = String((candidate.data as Record<string, unknown>).target);
              if (source === id && visible.has(target)) return true;
              if (target === id && visible.has(source)) return true;
              return false;
            })
            .map((candidate) => {
              const source = String((candidate.data as Record<string, unknown>).source);
              return source === id
                ? String((candidate.data as Record<string, unknown>).target)
                : source;
            })[0];
          // New nodes start beside a visible neighbour when one exists;
          // otherwise they spread around the centre on a sunflower spiral so a
          // force layout has a sane starting cloud instead of one point.
          let position: { x: number; y: number };
          if (neighbourId !== undefined) {
            const base = cy.getElementById(neighbourId).position();
            const jitter = ((index % 5) - 2) * 10;
            position = { x: base.x + jitter, y: base.y + jitter };
          } else {
            const slot = orphanIndex;
            orphanIndex += 1;
            const angle = slot * 2.399963;
            const radius = 26 + 16 * Math.sqrt(slot);
            position = {
              x: centre.x + Math.cos(angle) * radius,
              y: centre.y + Math.sin(angle) * radius,
            };
          }
          cy.getElementById(id).position(position);
          visible.add(id);
        });
        if (addedEdges.length > 0) cy.add(addedEdges);
      });

      if (!reducedMotion) {
        const freshIds = additions.map(elementId);
        cy.batch(() => {
          for (const id of freshIds) cy.getElementById(id).addClass("fresh");
        });
        if (fadeFrameRef.current !== null) cancelAnimationFrame(fadeFrameRef.current);
        fadeFrameRef.current = requestAnimationFrame(() => {
          fadeFrameRef.current = requestAnimationFrame(() => {
            if (!cyRef.current) return;
            cyRef.current.batch(() => {
              for (const id of freshIds) cyRef.current?.getElementById(id).removeClass("fresh");
            });
            fadeFrameRef.current = null;
          });
        });
      }

      cy.layout(layoutFor(layoutName, anchorId, reducedMotion, variant)).run();
    } else if (removedCount > 0) {
      cy.layout(layoutFor(layoutName, anchorId, reducedMotion, variant)).run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, anchorId, reducedMotion, variant]);

  useEffect(() => {
    return () => {
      if (fadeFrameRef.current !== null) cancelAnimationFrame(fadeFrameRef.current);
    };
  }, []);

  // Explicit re-layout request from the toolbar, and layout-name switches.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (layoutNonce === 0 && appliedLayoutRef.current === layoutName) return;
    appliedLayoutRef.current = layoutName;
    const layout = cy.layout(layoutFor(layoutName, anchorId, reducedMotion, variant));
    layout.run();
  }, [layoutNonce, layoutName, anchorId, reducedMotion, variant]);

  // Selection, path highlight and dimming are classes, so no re-layout happens.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("selected related dimmed on-path");
      if (highlightIds && highlightIds.size > 0) {
        for (const id of highlightIds) {
          cy.getElementById(id).addClass("on-path");
        }
      }
      if (dimUnhighlighted && highlightIds && highlightIds.size > 0) {
        cy.elements().not(".on-path").addClass("dimmed");
      }
      if (relatedIds && relatedIds.size > 0) {
        for (const id of relatedIds) {
          cy.getElementById(id).addClass("related");
        }
      }
      if (selection) {
        cy.getElementById(selection.id).addClass("selected");
      }
    });
  }, [selection, highlightIds, dimUnhighlighted, relatedIds, elements]);

  const runLayout = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.layout(layoutFor(layoutName, anchorId, reducedMotion, variant)).run();
  }, [layoutName, anchorId, reducedMotion, variant]);

  // Hand the camera controls to the toolbar that renders them.
  useEffect(() => {
    if (!controlsRef) return;
    controlsRef.current = {
      zoomIn: () => {
        const cy = cyRef.current;
        if (!cy) return;
        cy.zoom({
          level: Math.min(cy.maxZoom(), cy.zoom() * 1.25),
          renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
        });
      },
      zoomOut: () => {
        const cy = cyRef.current;
        if (!cy) return;
        cy.zoom({
          level: Math.max(cy.minZoom(), cy.zoom() / 1.25),
          renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
        });
      },
      fit: () => {
        cyRef.current?.fit(undefined, 26);
      },
      reset: () => {
        runLayout();
        cyRef.current?.fit(undefined, 26);
      },
    };
    return () => {
      controlsRef.current = null;
    };
  }, [controlsRef, runLayout]);

  const zoomIn = useCallback(() => {
    controlsRef?.current?.zoomIn();
  }, [controlsRef]);
  const zoomOut = useCallback(() => {
    controlsRef?.current?.zoomOut();
  }, [controlsRef]);
  const fit = useCallback(() => {
    controlsRef?.current?.fit();
  }, [controlsRef]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const cy = cyRef.current;
      if (!cy) return;
      const nodes = cy.nodes().toArray();
      if (nodes.length === 0) return;
      const currentIndex = selection
        ? nodes.findIndex((node) => node.id() === selection.id)
        : -1;
      const step = (delta: number): void => {
        const nextIndex =
          currentIndex === -1
            ? delta > 0
              ? 0
              : nodes.length - 1
            : (currentIndex + delta + nodes.length) % nodes.length;
        const next = nodes[nextIndex];
        if (next) onSelect({ kind: "node", id: next.id() as string });
      };
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        event.preventDefault();
        step(1);
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        event.preventDefault();
        step(-1);
      } else if (event.key === "Home") {
        event.preventDefault();
        const first = nodes[0];
        if (first) onSelect({ kind: "node", id: first.id() as string });
      } else if (event.key === "End") {
        event.preventDefault();
        const last = nodes[nodes.length - 1];
        if (last) onSelect({ kind: "node", id: last.id() as string });
      } else if (event.key === "Escape" && selection) {
        event.preventDefault();
        onSelect(null);
      } else if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        zoomIn();
      } else if (event.key === "-") {
        event.preventDefault();
        zoomOut();
      } else if (event.key === "0") {
        event.preventDefault();
        fit();
      }
    },
    [selection, onSelect, zoomIn, zoomOut, fit],
  );

  return (
    <div
      ref={containerRef}
      role="group"
      tabIndex={0}
      aria-label={label}
      aria-describedby={describedBy}
      onKeyDown={onKeyDown}
      data-testid="graph-canvas"
      className={cn(
        "relative min-h-[240px] flex-1 rounded-card bg-[#0a0b0b] outline-none",
        "bg-[radial-gradient(circle_at_1px_1px,rgba(230,231,229,0.055)_1px,transparent_0)] bg-[size:16px_16px]",
        className,
      )}
      style={{ minHeight: compact ? 220 : 400 }}
    >
      {zoom < 0.55 ? (
        <p className="pointer-events-none absolute bottom-2 left-3 text-[10.5px] text-muted-foreground">
          Zoomed out. Entity names reveal as you zoom in.
        </p>
      ) : null}
    </div>
  );
}

/** Imperative handle for the toolbar: the camera controls it drives. */
export interface GraphControls {
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
  reset: () => void;
}

export function describeNode(node: GraphNode): string {
  return `${node.typeLabel}: ${node.identifier}`;
}

export function describeEdge(edge: GraphEdge): string {
  return edge.kind === "SENT"
    ? `Transfer ${edge.transactionId} was sent by the source account`
    : `Transfer ${edge.transactionId} was received by the target account`;
}
