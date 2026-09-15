import { useEffect, useMemo, useRef } from "react";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import {
  type EvalPreload,
  type EvalPreloadStatus,
  evalPreloadRefetchInterval,
  getEvalPreload,
  isActiveEvalPreloadStatus,
} from "@/lib/eval-preload";

export interface CapabilityEvalPreloadState {
  capabilityId: string;
  preload: EvalPreload | null;
  status: EvalPreloadStatus | null;
  error: string | null;
  counts: Record<string, number> | undefined;
}

function selectCapabilityEvalPreload(
  capabilityId: string,
  capability: Awaited<ReturnType<typeof apiClient.capabilities.capabilitiesRetrieve>>
): CapabilityEvalPreloadState {
  const preload = getEvalPreload(capability);
  return {
    capabilityId,
    counts: preload?.counts,
    error: preload?.error ?? null,
    preload,
    status: preload?.status ?? null,
  };
}

function invalidateEvalDataOnReady(
  qc: ReturnType<typeof useQueryClient>,
  projectId: string | undefined
) {
  if (!projectId) return;
  void qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
  void qc.invalidateQueries({ queryKey: ["evaluator-catalog", projectId] });
}

/** Poll capability detail for ``improvementMetadata.eval_preload`` until terminal. */
export function useCapabilityEvalPreload(
  capabilityId: string | undefined,
  options?: { enabled?: boolean; projectId?: string }
) {
  const qc = useQueryClient();
  const projectId = options?.projectId;
  const enabled = (options?.enabled ?? true) && !!capabilityId;
  const prevStatus = useRef<EvalPreloadStatus | null>(null);

  const query = useQuery({
    enabled,
    queryFn: () => apiClient.capabilities.capabilitiesRetrieve({ id: capabilityId! }),
    queryKey: ["capability-eval-preload", capabilityId],
    refetchInterval: (q) =>
      evalPreloadRefetchInterval(getEvalPreload(q.state.data ?? {})?.status ?? null),
  });

  const data = useMemo(
    () =>
      query.data && capabilityId
        ? selectCapabilityEvalPreload(capabilityId, query.data)
        : undefined,
    [capabilityId, query.data]
  );

  useEffect(() => {
    const status = data?.status ?? null;
    if (prevStatus.current !== "ready" && status === "ready") {
      invalidateEvalDataOnReady(qc, projectId);
    }
    prevStatus.current = status;
  }, [data?.status, projectId, qc]);

  return {
    ...query,
    data,
    isActive: isActiveEvalPreloadStatus(data?.status),
  };
}
