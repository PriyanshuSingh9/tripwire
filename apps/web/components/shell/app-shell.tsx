"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";

import { BrandMark, SidebarNav, SyntheticReplayNotice } from "@/components/shell/sidebar";
import { navItemForPath } from "@/components/shell/nav";
import { SystemStatusCluster } from "@/components/shell/system-status";
import { cn } from "@/lib/utils";

/**
 * Persistent application shell: sidebar, header and content region.
 *
 * The sidebar and header stay put while the content region changes between
 * routes, so position and context survive navigation.
 */
export function AppShell({ children }: { children: React.ReactNode }): React.JSX.Element {
  const pathname = usePathname();
  const current = navItemForPath(pathname);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [navOpen, setNavOpen] = useState(false);

  // The drawer is a native dialog, so opening and closing is an external-system
  // update driven by one piece of state. Route changes close it through the
  // navigation links themselves.
  useEffect(() => {
    const node = dialogRef.current;
    if (!node) return;
    if (navOpen && !node.open) node.showModal();
    if (!navOpen && node.open) node.close();
  }, [navOpen]);

  return (
    <div className="flex min-h-dvh bg-background">
      <a
        href="#workspace-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-card focus:px-3 focus:py-2 focus:text-[13px] focus:text-foreground"
      >
        Skip to workspace
      </a>

      {/* Persistent desktop sidebar */}
      <div className="sticky top-0 hidden h-dvh w-[204px] shrink-0 flex-col border-r border-border bg-card/60 lg:flex">
        <BrandMark />
        <SidebarNav className="flex-1 overflow-y-auto" />
        <SyntheticReplayNotice />
      </div>

      {/* Compact navigation drawer */}
      <dialog
        ref={dialogRef}
        aria-label="Primary navigation"
        onCancel={(event) => {
          event.preventDefault();
          setNavOpen(false);
        }}
        onClose={() => setNavOpen(false)}
        className={cn(
          "m-0 h-dvh max-h-none w-[248px] max-w-none border-r border-border bg-card p-0 text-foreground",
          "backdrop:bg-black/60",
        )}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between">
            <BrandMark />
            <button
              type="button"
              onClick={() => setNavOpen(false)}
              aria-label="Close navigation"
              className="mt-4 mr-3 rounded-control p-1 text-muted-foreground hover:bg-surface-hover hover:text-foreground"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </div>
          <SidebarNav className="flex-1 overflow-y-auto" onNavigate={() => setNavOpen(false)} />
          <SyntheticReplayNotice />
        </div>
      </dialog>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur-[2px]">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <button
                type="button"
                onClick={() => setNavOpen(true)}
                aria-label="Open navigation"
                aria-expanded={navOpen}
                aria-controls="primary-navigation"
                className="rounded-control border border-border-strong bg-secondary p-1.5 text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:text-foreground lg:hidden"
              >
                <Menu className="size-4" aria-hidden="true" />
              </button>
              <div className="min-w-0">
                <h1 className="truncate text-[19px] leading-tight font-semibold tracking-[-0.01em] text-foreground">
                  {current.title}
                </h1>
                <p className="mt-0.5 hidden truncate text-[12px] text-muted-foreground sm:block">
                  {current.description}
                </p>
              </div>
            </div>
            <SystemStatusCluster />
          </div>
        </header>

        <main id="workspace-content" className="min-w-0 flex-1 px-4 py-4 sm:px-6 sm:py-5">
          {children}
        </main>
      </div>
    </div>
  );
}
