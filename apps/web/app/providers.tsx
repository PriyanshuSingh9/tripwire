"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Query client defaults.
 *
 * A failed request is never retried into a fake success: retries are limited
 * to one attempt for transient transport errors, and every surface renders the
 * error state the failure deserves. `staleTime` is set per hook.
 */
function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          if (failureCount >= 1) return false;
          // Do not retry a correct refusal: only transport faults.
          const status = (error as { status?: number }).status;
          return status === 0 || status === undefined;
        },
        refetchOnWindowFocus: false,
        gcTime: 5 * 60_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function Providers({ children }: { children: ReactNode }): React.JSX.Element {
  const [client] = useState(createQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
