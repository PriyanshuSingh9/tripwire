import { Skeleton } from "@/components/ui/states";

/**
 * Route-level fallback.
 *
 * It names what is loading rather than showing a spinner, so the shell never
 * looks like a page that failed to arrive.
 */
export function PageFallback({ label }: { label: string }): React.JSX.Element {
  return (
    <div className="space-y-4" role="status" aria-live="polite">
      <p className="text-[12px] text-muted-foreground">{label}</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="rounded-card border border-border bg-card px-4 py-3.5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-3 h-7 w-20" />
            <Skeleton className="mt-3 h-3 w-full" />
          </div>
        ))}
      </div>
      <div className="rounded-card border border-border bg-card p-4">
        <Skeleton className="h-3.5 w-40" />
        <div className="mt-3 space-y-2">
          {[0, 1, 2, 3, 4].map((index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}
