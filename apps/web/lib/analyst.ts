"use client";

import { useSyncExternalStore } from "react";

/**
 * Analyst identifier for actions that require provenance.
 *
 * Every case action the API accepts carries a reviewer or opener identifier.
 * It is typed by the person using the workstation and kept in this browser
 * only, so the UI never invents a name and never sends a default.
 */

const STORAGE_KEY = "traceaml.analyst-id.v1";
const MAX_LENGTH = 64;

let analystId = "";
let loaded = false;
const listeners = new Set<() => void>();

function load(): void {
  if (loaded) return;
  loaded = true;
  if (typeof window === "undefined") return;
  try {
    analystId = window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    analystId = "";
  }
}

function emit(): void {
  for (const listener of listeners) listener();
}

export function setAnalystId(next: string): void {
  load();
  analystId = next.slice(0, MAX_LENGTH);
  if (typeof window !== "undefined") {
    try {
      if (analystId.length === 0) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, analystId);
    } catch {
      // Storage may be blocked; the value still works for this page session.
    }
  }
  emit();
}

export function getAnalystId(): string {
  load();
  return analystId;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAnalystId(): string {
  return useSyncExternalStore(subscribe, getAnalystId, () => "");
}

export function looksLikeIdentifier(value: string): boolean {
  return value.trim().length > 0 && value.trim().length <= MAX_LENGTH;
}
