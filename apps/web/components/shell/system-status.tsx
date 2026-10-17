"use client";

import { CircleDashed, Cpu } from "lucide-react";

import { FalkorBadge } from "@/components/ui/semantic";
import { Chip } from "@/components/ui/badge";
import { useHealth } from "@/lib/api/hooks";

/**
 * Header status cluster.
 *
 * Everything here is read from `GET /api/health`: the API version, the
 * FalkorDB probe result, and whether an LLM provider is configured. When the
 * probe itself cannot run, the cluster says so instead of implying health.
 */
export function SystemStatusCluster(): React.JSX.Element {
  const health = useHealth(30_000);

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Chip mono title="Service version reported by GET /api/health">
        {health.data ? `service ${health.data.version}` : health.isPending ? "service …" : "service status unknown"}
      </Chip>
      {health.isPending ? (
        <Chip title="Probing the graph dependency">
          <CircleDashed className="mr-1 size-3 animate-spin" aria-hidden="true" />
          graph …
        </Chip>
      ) : health.isError ? (
        <Chip title="GET /api/health did not answer, so dependency status is unknown">
          <Cpu className="mr-1 size-3" aria-hidden="true" />
          dependencies unknown
        </Chip>
      ) : (
        <>
          <FalkorBadge status={health.data.falkordb} />
          <Chip
            title={
              health.data.llm === "configured"
                ? "A provider and key are set; the agent runs only when a request opts in."
                : "No provider or key is configured; the deterministic report is used."
            }
          >
            llm {health.data.llm === "configured" ? "configured" : "off"}
          </Chip>
        </>
      )}
    </div>
  );
}
