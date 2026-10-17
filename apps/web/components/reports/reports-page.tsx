"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { BenchmarksPage } from "@/components/benchmarks/benchmarks-page";
import { SystemPage } from "@/components/system/system-page";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "reports", label: "Reports", href: "/reports?tab=reports" },
  { id: "system", label: "System", href: "/reports?tab=system" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/**
 * One secondary destination for advanced material: recorded model reports and
 * service diagnostics. The section lives in the URL so links stay shareable.
 */
export function ReportsPage(): React.JSX.Element {
  const searchParams = useSearchParams();
  const requested = searchParams.get("tab");
  const active: TabId = requested === "system" ? "system" : "reports";

  return (
    <div className="space-y-4">
      <nav
        aria-label="Reports and System sections"
        className="flex w-fit flex-wrap items-center gap-1 rounded-control border border-border bg-card p-1"
      >
        {TABS.map((tab) => {
          const selected = tab.id === active;
          return (
            <Link
              key={tab.id}
              href={tab.href}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "rounded-[5px] px-3 py-1.5 text-[12.5px] transition-colors duration-[var(--motion-fast)]",
                selected
                  ? "bg-surface-active font-medium text-foreground"
                  : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {active === "reports" ? <BenchmarksPage /> : <SystemPage />}
    </div>
  );
}
