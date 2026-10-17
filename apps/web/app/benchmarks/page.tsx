import { redirect } from "next/navigation";

/**
 * Compatibility route.
 *
 * Benchmarks now sits inside the Reports section of `/reports`; old bookmarks
 * land on that section with nothing to re-learn.
 */
export default async function Page(): Promise<never> {
  redirect("/reports?tab=reports");
}
