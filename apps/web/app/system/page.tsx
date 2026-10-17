import { redirect } from "next/navigation";

/**
 * Compatibility route.
 *
 * Service health and diagnostics now sit inside the System section of
 * `/reports`; old bookmarks land on that section.
 */
export default async function Page(): Promise<never> {
  redirect("/reports?tab=system");
}
