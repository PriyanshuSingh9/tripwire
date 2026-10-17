import {
  BarChart3,
  BellRing,
  FolderSearch,
  LayoutDashboard,
  Share2,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  title: string;
  description: string;
}

export interface NavSection {
  /** Omitted for the primary group. */
  label?: string;
  items: NavItem[];
}

/**
 * Four primary destinations: Home, Fraud alerts, Cases, Reports & System.
 * The advanced workspace tools stay reachable but out of the novice path.
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    items: [
      {
        href: "/",
        label: "Home",
        icon: LayoutDashboard,
        title: "Fraud overview",
        description: "What needs attention and where to start reviewing.",
      },
      {
        href: "/alerts",
        label: "Fraud alerts",
        icon: BellRing,
        title: "Fraud alerts",
        description: "Flagged transactions ranked by the scoring model, ready for review.",
      },
      {
        href: "/cases",
        label: "Cases",
        icon: FolderSearch,
        title: "Cases",
        description: "Investigations you are tracking and their review history.",
      },
      {
        href: "/reports",
        label: "Reports & System",
        icon: BarChart3,
        title: "Reports & System",
        description: "Recorded model performance, service health and diagnostics.",
      },
    ],
  },
  {
    label: "Advanced",
    items: [
      {
        href: "/simulation",
        label: "Advanced workspace",
        icon: Share2,
        title: "Advanced workspace",
        description: "Run the pipeline with explicit tool and traversal options.",
      },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);

/**
 * Routes that are not primary destinations but still need a header identity.
 * The guided investigation opens from an alert or a case.
 */
const CONTEXT_ITEMS: NavItem[] = [
  {
    href: "/investigation",
    label: "Investigation",
    icon: SlidersHorizontal,
    title: "Investigation",
    description: "Evidence-backed review of one flagged transaction.",
  },
];

export function navItemForPath(pathname: string): NavItem {
  if (pathname === "/") return NAV_ITEMS[0]!;
  const context = CONTEXT_ITEMS.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  if (context) return context;
  const match = NAV_ITEMS.find(
    (item) => item.href !== "/" && (pathname === item.href || pathname.startsWith(`${item.href}/`)),
  );
  if (match) return match;
  // Folded destinations keep their old deep links, so they resolve here too.
  if (pathname.startsWith("/benchmarks") || pathname.startsWith("/system")) {
    return NAV_ITEMS.find((item) => item.href === "/reports") ?? NAV_ITEMS[0]!;
  }
  return NAV_ITEMS[0]!;
}

export function isActivePath(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/reports") {
    return (
      pathname === "/reports" ||
      pathname.startsWith("/reports/") ||
      pathname.startsWith("/benchmarks") ||
      pathname.startsWith("/system")
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
