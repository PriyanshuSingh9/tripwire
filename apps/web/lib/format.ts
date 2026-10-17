/**
 * Presentation helpers. Timestamps from the replay dataset have minute
 * resolution and no zone, so they are parsed and displayed as UTC and the
 * column header says so. Nothing here invents precision the data lacks.
 */

const UTC_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const UTC_TIME_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const NUMBER_FORMATTER = new Intl.NumberFormat("en-US");

export function parseApiTimestamp(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(trimmed);
  const iso = trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
  const ms = Date.parse(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(ms) ? null : new Date(ms);
}

/** "12 Sep 2022 00:04 UTC" — falls back to the raw string when unparsable. */
export function formatTimestamp(raw: string | null | undefined): string {
  const date = parseApiTimestamp(raw);
  if (date === null) return raw && raw.trim().length > 0 ? raw : "—";
  return `${UTC_FORMATTER.format(date)} UTC`;
}

export function formatTimeOnly(raw: string | null | undefined): string {
  const date = parseApiTimestamp(raw);
  if (date === null) return "—";
  return UTC_TIME_FORMATTER.format(date);
}

export function formatRelative(raw: string | null | undefined, now: Date = new Date()): string {
  const date = parseApiTimestamp(raw);
  if (date === null) return "—";
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatTimestamp(raw);
}

/** Model and policy scores are probabilities in [0, 1]. */
export function formatScore(score: number): string {
  return `${(score * 100).toFixed(2)}%`;
}

export function formatFraction(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatNumber(value: number): string {
  return NUMBER_FORMATTER.format(value);
}

export function formatCompact(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 10_000) return `${(value / 1_000).toFixed(1)}k`;
  return NUMBER_FORMATTER.format(value);
}

export function formatLatency(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(2)} s`;
  if (ms >= 10) return `${ms.toFixed(1)} ms`;
  return `${ms.toFixed(2)} ms`;
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1_000) return `${Math.round(ms)} ms`;
  return `${(ms / 1_000).toFixed(1)} s`;
}

/** Amounts arrive paired with their own source currency. */
export function formatAmount(
  amount: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (amount === null || amount === undefined) return "—";
  const formatted = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return currency ? `${formatted} ${currency}` : formatted;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0";
  if (Math.abs(value) < 0.0001) return `$${value.toExponential(2)}`;
  return `$${value.toFixed(4)}`;
}

/** Account keys are `bank::account`; both parts are source-backed. */
export function splitAccountKey(accountKey: string): { bank: string; account: string } {
  const separator = accountKey.indexOf("::");
  if (separator === -1) return { bank: "", account: accountKey };
  return {
    bank: accountKey.slice(0, separator),
    account: accountKey.slice(separator + 2),
  };
}

export function truncateMiddle(value: string, max = 28): string {
  if (value.length <= max) return value;
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return `${value.slice(0, head)}…${value.slice(value.length - tail)}`;
}

export function titleCase(value: string): string {
  return value
    .split(/[_\s]+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
