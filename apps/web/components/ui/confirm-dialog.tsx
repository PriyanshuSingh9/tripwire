"use client";

import { useEffect, useId, useRef } from "react";

import { Button, type ButtonVariant } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Confirmation dialog for a consequential action.
 *
 * Built on the native `<dialog>` element, so focus trapping, the inert
 * background, Escape-to-cancel and focus restoration come from the platform
 * rather than from re-implemented key handling.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  confirmVariant = "primary",
  onConfirm,
  onCancel,
  children,
  pending = false,
  error,
}: {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  confirmVariant?: ButtonVariant;
  onConfirm: () => void;
  onCancel: () => void;
  children?: React.ReactNode;
  pending?: boolean;
  error?: React.ReactNode;
}): React.JSX.Element {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const node = dialogRef.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onCancel();
      }}
      onClose={() => {
        if (open && !pending) onCancel();
      }}
      className={cn(
        "w-[min(92vw,30rem)] rounded-card border border-border bg-card p-0 text-foreground",
        "backdrop:bg-black/60 backdrop:backdrop-blur-[1px]",
      )}
    >
      <div className="space-y-3 px-5 py-4">
        <h2 id={titleId} className="text-[14px] font-semibold text-foreground">
          {title}
        </h2>
        {description ? (
          <div className="text-[12.5px] leading-relaxed text-muted-foreground">{description}</div>
        ) : null}
        {children}
        {error ? (
          <p role="alert" className="rounded-control border border-critical/40 bg-critical/10 px-2.5 py-2 text-[12px] text-critical">
            {error}
          </p>
        ) : null}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          {cancelLabel}
        </Button>
        <Button variant={confirmVariant} onClick={onConfirm} loading={pending}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
