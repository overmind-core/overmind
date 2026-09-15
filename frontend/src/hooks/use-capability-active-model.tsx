import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { count, formatNumber } from "@/lib/formatters";
import { notify } from "@/lib/notify";
import type { Capability, DeployedModel } from "@/openapi";
import { ResponseError } from "@/openapi";

export function blockedReason(status: string): string | null {
  if (status === "ready") return null;
  if (status === "failed") return "This deployment failed, so it can't serve the alias.";
  if (status === "deleting" || status === "deleted") return "This deployment has been torn down.";
  return "Still deploying — it can serve the alias once it is ready.";
}

/** A shorter window breaks callers whose prompts fitted the incumbent, and vLLM
 * reports the overflow as an opaque 502 — warned about rather than blocked,
 * since a cheaper, smaller model is often the point. */
const isNarrowing = (candidate: DeployedModel, incumbent: DeployedModel): boolean =>
  candidate.maxModelLen < incumbent.maxModelLen;

/** `setLive` asks first when the switch shrinks the context window, so the
 * caller must render `narrowingConfirm` for that question to be asked. */
export const useSetActiveModel = (capabilityId: string) => {
  const queryClient = useQueryClient();
  const [narrowing, setNarrowing] = useState<{
    candidate: DeployedModel;
    incumbent: DeployedModel;
  } | null>(null);
  const mutation = useMutation({
    mutationFn: (model: DeployedModel) =>
      apiClient.capabilities
        .capabilitiesPartialUpdate({
          id: capabilityId,
          patchedCapabilityRequest: { activeModel: model.id },
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            // A tenancy violation comes back as a field error, not a `detail`.
            throw new Error(r.detail ?? r.active_model?.[0] ?? "Couldn't switch the live model");
          }
          throw error;
        }),
    onError: (e) => notify.error(e, "Couldn't switch the live model"),
    onSuccess: (_data, model) => {
      notify.success("Live model switched", `The alias now answers with ${model.modelId}.`);
      // Seed before invalidating: `isPending` goes false when the PATCH
      // resolves, before the refetch lands, so a control reading `activeModel`
      // off the cache would jump back to the old row for one round trip.
      queryClient.setQueryData(
        ["capability-detail", capabilityId],
        (prev: Capability | undefined) => (prev ? { ...prev, activeModel: model.id } : prev)
      );
      queryClient.invalidateQueries({ queryKey: ["capability-detail", capabilityId] });
      queryClient.invalidateQueries({ queryKey: ["capabilities"] });
    },
  });

  return {
    error: mutation.error,
    isPending: mutation.isPending,
    narrowingConfirm: (
      <ConfirmDialog
        confirmLabel="Switch anyway"
        description={
          narrowing
            ? `${narrowing.candidate.modelId} accepts ${count(narrowing.candidate.maxModelLen, "token")} of context, down from ${formatNumber(narrowing.incumbent.maxModelLen)} on ${narrowing.incumbent.modelId}. Callers sending longer prompts will start failing.`
            : undefined
        }
        // The toast can fade unread, so the dialog that is still asking for the
        // decision restates the reason.
        error={mutation.error}
        isPending={mutation.isPending}
        // The user opted into a known risk: a failure must not look like it
        // went through.
        keepOpenOnError
        onConfirm={async () => {
          if (narrowing) await mutation.mutateAsync(narrowing.candidate);
        }}
        onOpenChange={(open) => !open && setNarrowing(null)}
        open={!!narrowing}
        title="Smaller context window"
      />
    ),
    setLive: (candidate: DeployedModel, incumbent: DeployedModel | null) => {
      if (incumbent && isNarrowing(candidate, incumbent)) setNarrowing({ candidate, incumbent });
      else mutation.mutate(candidate);
    },
    variables: mutation.variables,
  };
};
