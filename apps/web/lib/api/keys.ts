/** Central query keys so cache invalidation stays predictable. */

export const queryKeys = {
  health: () => ["health"] as const,
  alerts: (limit: number, offset: number) => ["alerts", limit, offset] as const,
  benchmarks: () => ["benchmarks"] as const,
  cases: (status: string | null, limit: number, offset: number) =>
    ["cases", status, limit, offset] as const,
  case: (caseId: string) => ["case", caseId] as const,
};
