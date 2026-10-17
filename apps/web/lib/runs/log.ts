"use client";

/**
 * Browser-local record of investigation attempts made from this workstation.
 *
 * The API stores no run, event, or failure history: there is no endpoint that
 * lists past investigations. Overview and System therefore report only what
 * this browser actually observed, and every surface that reads this log labels
 * it as local session history. Entries are written from real API responses and
 * real HTTP errors, never from optimistic assumptions.
 */

import { useSyncExternalStore } from "react";

import type {
  CaseActionResult,
  InvestigationOutcome,
  PolicyDecision,
} from "@/lib/api/types";

const STORAGE_KEY = "traceaml.investigation-log.v1";
const MAX_ENTRIES = 50;

export interface InvestigationLogEntry {
  attempt_id: string;
  attempted_at: string;
  transaction_id: string;
  status: "ok" | "error";
  investigation_id: string | null;
  outcome: InvestigationOutcome | null;
  decision: PolicyDecision | null;
  risk_score: number | null;
  client_duration_ms: number;
  http_status: number | null;
  error_detail: string | null;
  evidence_counts: { transactions: number; accounts: number; paths: number } | null;
  case_id: string | null;
  action_status: CaseActionResult["status"] | null;
  tool_run_count: number | null;
}

const EMPTY: InvestigationLogEntry[] = [];

let entries: InvestigationLogEntry[] = EMPTY;
let loaded = false;
const listeners = new Set<() => void>();

function isEntry(value: unknown): value is InvestigationLogEntry {
  if (value === null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.attempt_id === "string" &&
    typeof record.transaction_id === "string" &&
    (record.status === "ok" || record.status === "error")
  );
}

function load(): void {
  if (loaded) return;
  loaded = true;
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed: unknown = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      entries = parsed.filter(isEntry).slice(0, MAX_ENTRIES);
    }
  } catch {
    // A corrupt or unavailable store must never break the page.
    entries = EMPTY;
  }
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage may be full or blocked; the in-memory log still works.
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

export function appendInvestigation(entry: InvestigationLogEntry): void {
  load();
  entries = [entry, ...entries].slice(0, MAX_ENTRIES);
  persist();
  emit();
}

export function clearInvestigations(): void {
  load();
  entries = EMPTY;
  persist();
  emit();
}

export function getInvestigations(): InvestigationLogEntry[] {
  load();
  return entries;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): InvestigationLogEntry[] {
  load();
  return entries;
}

function getServerSnapshot(): InvestigationLogEntry[] {
  return EMPTY;
}

/** Reactive read of this browser's investigation log. */
export function useInvestigationLog(): InvestigationLogEntry[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function newAttemptId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `attempt-${crypto.randomUUID().slice(0, 8)}`;
  }
  return `attempt-${Math.random().toString(36).slice(2, 10)}`;
}
