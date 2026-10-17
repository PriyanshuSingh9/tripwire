"use client";

/**
 * Cache of the most recent successful investigation run for this tab.
 *
 * It exists so the run survives navigation: returning to Simulation restores
 * the graph and evidence instead of an empty form, and Overview can preview the
 * same run. The stored payload is a real API response with the request that
 * produced it; nothing is synthesised, and a payload that does not look like an
 * investigation is discarded.
 */

import { useSyncExternalStore } from "react";

import type { InvestigationRequest, InvestigationResponse } from "@/lib/api/types";

const STORAGE_KEY = "traceaml.last-run.v1";

export interface CachedRun {
  request: InvestigationRequest;
  response: InvestigationResponse;
  at: string;
}

let cached: CachedRun | null = null;
let loaded = false;
const listeners = new Set<() => void>();

function looksLikeInvestigation(value: unknown): value is InvestigationResponse {
  if (value === null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const evidence = record.evidence as Record<string, unknown> | undefined;
  return (
    typeof record.investigation_id === "string" &&
    typeof record.transaction_id === "string" &&
    typeof record.risk_score === "number" &&
    evidence !== undefined &&
    evidence !== null &&
    Array.isArray(evidence.transactions) &&
    Array.isArray(evidence.accounts) &&
    Array.isArray(evidence.paths)
  );
}

function looksLikeRequest(value: unknown): value is InvestigationRequest {
  if (value === null || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return typeof record.transaction_id === "string" && typeof record.max_hops === "number";
}

function parse(raw: string): CachedRun | null {
  const parsed: unknown = JSON.parse(raw);
  if (parsed === null || typeof parsed !== "object") return null;
  const record = parsed as Record<string, unknown>;
  if (!looksLikeInvestigation(record.response) || !looksLikeRequest(record.request)) return null;
  return {
    request: record.request,
    response: record.response,
    at: typeof record.at === "string" ? record.at : new Date().toISOString(),
  };
}

function load(): void {
  if (loaded) return;
  loaded = true;
  if (typeof window === "undefined") return;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    cached = raw ? parse(raw) : null;
  } catch {
    cached = null;
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

export function saveLastRun(run: CachedRun): void {
  load();
  cached = run;
  if (typeof window !== "undefined") {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(run));
    } catch {
      // The in-memory copy still serves the current page.
    }
  }
  emit();
}

export function clearLastRun(): void {
  load();
  cached = null;
  if (typeof window !== "undefined") {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // Nothing to do: the in-memory copy is already cleared.
    }
  }
  emit();
}

export function getLastRun(): CachedRun | null {
  load();
  return cached;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLastRun(): CachedRun | null {
  return useSyncExternalStore(subscribe, getLastRun, () => null);
}
