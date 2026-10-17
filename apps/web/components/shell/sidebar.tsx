"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { isActivePath, NAV_SECTIONS } from "@/components/shell/nav";
import { cn } from "@/lib/utils";

/**
 * Slim primary navigation.
 *
 * The active item is marked three ways: an orange indicator bar, a raised
 * surface, and `aria-current="page"` for assistive technology. Hover and focus
 * are the same weight so keyboard users see the state mouse users see.
 */
export function SidebarNav({
  onNavigate,
  className,
}: {
  onNavigate?: () => void;
  className?: string;
}): React.JSX.Element {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className={cn("flex flex-col gap-3 px-2", className)}>
      {NAV_SECTIONS.map((section) => (
        <div key={section.label ?? "primary"} className="flex flex-col gap-0.5">
          {section.label ? (
            <p className="px-2.5 pt-1 pb-0.5 text-[10.5px] font-medium tracking-[0.06em] text-muted-foreground/80 uppercase">
              {section.label}
            </p>
          ) : null}
          {section.items.map((item) => {
            const active = isActivePath(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group relative flex items-center gap-2.5 rounded-control px-2.5 py-2 text-[13px] transition-colors duration-[var(--motion-fast)]",
                  active
                    ? "bevel-neutral bg-surface-active font-medium text-foreground"
                    : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full transition-opacity duration-[var(--motion-fast)]",
                    active ? "bg-primary opacity-100" : "opacity-0",
                  )}
                />
                <Icon
                  className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")}
                  aria-hidden="true"
                />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

export function BrandMark(): React.JSX.Element {
  return (
    <div className="flex items-center gap-2 px-4 py-4">
      <span
        aria-hidden="true"
        className="flex size-7 items-center justify-center rounded-[7px] border border-primary/40 bg-primary/12"
      >
        <span className="size-2 rounded-[2px] bg-primary" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-semibold tracking-[0.01em] text-foreground">
          TraceAML
        </span>
        <span className="block truncate text-[10.5px] tracking-[0.06em] text-muted-foreground uppercase">
          Fraud review
        </span>
      </span>
    </div>
  );
}

/**
 * The interface replays a synthetic dataset. Saying so in the shell keeps the
 * reader from mistaking graph evidence for a live banking integration.
 */
export function SyntheticReplayNotice(): React.JSX.Element {
  return (
    <p className="mx-3 mb-3 rounded-control border border-border bg-muted px-2.5 py-2 text-[10.5px] leading-relaxed text-muted-foreground">
      Replays a fixed snapshot of synthetic bank data. Not a live banking feed.
    </p>
  );
}
