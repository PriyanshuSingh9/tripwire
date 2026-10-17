"use client";

import { useCallback, useEffect, useRef } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Non-modal side panel for contextual detail (entity, relationship, evidence,
 * case, alert, diagnostic).
 *
 * It is not modal on purpose: the list or graph behind it stays interactive so
 * the reader keeps their place. Escape closes it, focus moves into the panel
 * when it opens, and focus returns to whatever opened it on close.
 */
export function SidePanel({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = "full",
  tone = "default",
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: "full" | "half";
  tone?: "default" | "muted";
}): React.JSX.Element | null {
  const panelRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = panelRef.current;
    if (node) {
      node.focus({ preventScroll: true });
    }
    return () => {
      const target = restoreFocusRef.current;
      if (target && document.contains(target)) {
        target.focus({ preventScroll: true });
      }
    };
  }, [open]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    },
    [onClose],
  );

  if (!open) return null;

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      aria-label={typeof title === "string" ? title : undefined}
      onKeyDown={handleKeyDown}
      className={cn(
        "panel-reveal flex min-h-0 flex-col overflow-hidden rounded-card border border-border",
        tone === "muted" ? "bg-muted" : "bg-card surface-raised",
        width === "half" ? "w-full" : "w-full",
        "focus:outline-none",
      )}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-[13px] font-semibold text-foreground">{title}</h2>
          {subtitle ? (
            <div className="mt-0.5 text-[11px] text-muted-foreground">{subtitle}</div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details panel"
          className="shrink-0 rounded-control p-1 text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:bg-surface-hover hover:text-foreground"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3.5">
        {children}
      </div>
      {footer ? (
        <footer className="border-t border-border px-4 py-3">{footer}</footer>
      ) : null}
    </aside>
  );
}

/** Disclosure used for progressive technical detail inside panels and cards. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className,
}: {
  summary: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
}): React.JSX.Element {
  return (
    <details open={defaultOpen} className={cn("group border-t border-border pt-3", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12px] font-medium text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:text-foreground">
        <span
          aria-hidden="true"
          className="inline-block transition-transform duration-[var(--motion-fast)] group-open:rotate-90"
        >
          ▸
        </span>
        {summary}
      </summary>
      <div className="mt-2.5 space-y-2">{children}</div>
    </details>
  );
}
