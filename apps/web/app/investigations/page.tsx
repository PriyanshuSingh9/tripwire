import { redirect } from "next/navigation";

/**
 * Compatibility route.
 *
 * The guided workspace is served at `/investigation`; the previously linked
 * path is the plural. The query string is carried over so a deep link that
 * names a transaction still opens the same anchor after the redirect.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<never> {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string") query.set(key, value);
    else if (Array.isArray(value)) {
      for (const item of value) query.append(key, item);
    }
  }
  const suffix = query.toString();
  redirect(suffix.length > 0 ? `/investigation?${suffix}` : "/investigation");
}
