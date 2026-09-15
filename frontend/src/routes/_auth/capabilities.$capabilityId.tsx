import { useEffect, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import z from "zod";

import apiClient from "@/client";
import { CapabilityNameEditor } from "@/components/capability-detail/CapabilityNameEditor";
import { DatasetTab } from "@/components/capability-detail/DatasetTab";
import { EvaluatorsTab } from "@/components/capability-detail/EvaluatorsTab";
import { ModelsTab } from "@/components/capability-detail/ModelsTab";
import { CapabilityNextStepNudge } from "@/components/capability-detail/next-step-nudge";
import { CapabilityFactsBar } from "@/components/capability-overview/CapabilityFactsBar";
import { CapabilityPromptCard } from "@/components/capability-overview/CapabilityPromptCard";
import { ComponentsExplorer } from "@/components/capability-overview/ComponentsExplorer";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { lazyChart } from "@/components/ui/lazy-chart";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuthContext } from "@/contexts/auth-context";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useCapabilityDetailQuery } from "@/hooks/use-query";
import { invalidateCapabilityQueries } from "@/lib/capability-queries";
import { featureFlags } from "@/lib/feature-flags";
import { notify } from "@/lib/notify";
import { PROSE, TITLE } from "@/lib/typography";
import { ResponseError } from "@/openapi";

// @xyflow/react (+ its CSS) is heavy and only used on this tab — load it lazily.
const TrajectoryFlowTab = lazyChart(
  () =>
    import("@/components/capability-detail/flow/TrajectoryFlowTab").then((m) => ({
      default: m.TrajectoryFlowTab,
    })),
  { minHeight: 400 }
);

// The page scrolls as one document: `tab` survives only as a deep-link anchor
// resolved to a section scroll.
const CAPABILITY_SECTIONS = ["overview", "dataset", "evaluators", "models"] as const;

export const Route = createFileRoute("/_auth/capabilities/$capabilityId")({
  component: CapabilityDetailPage,
  validateSearch: z.object({
    // `_auth.tsx` hides the shared header while this is set.
    flowFull: z.boolean().optional(),
    projectId: z.string().optional(),
    // `.catch` so links with retired values (`tab=jobs`) fall back quietly.
    tab: z.enum(CAPABILITY_SECTIONS).optional().catch(undefined),
  }),
});

function CopyIdButton({ label, value }: { label: string; value: string }) {
  const { copied, copy } = useCopy(value);
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-label={label}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-sm bg-control px-1.5 py-0.5 font-mono text-sm text-foreground transition-colors duration-150 hover:bg-control-hover focus-visible:ring-[2px] focus-visible:ring-ring/60 outline-none"
            onClick={copy}
            type="button"
          >
            {value.slice(0, 8)}…
            {copied ? (
              <Icon.success className="size-3.5 text-success" />
            ) : (
              <Icon.copy className="size-3.5 text-muted-foreground" />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{copied ? "Copied!" : label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function CapabilityDetailPage() {
  const { capabilityId } = Route.useParams();
  const queryClient = useQueryClient();
  const { tab, projectId: projectIdParam, flowFull } = Route.useSearch();
  const navigate = Route.useNavigate();
  const setFlowFull = (next: boolean) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, flowFull: next ? true : undefined }),
    });

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const { isGuest } = useAuthContext();
  const guard = useGuestGate();
  const { data, isLoading, error } = useCapabilityDetailQuery(capabilityId);

  // Keyed on data PRESENCE, not identity: the detail query polls, and a `data`
  // dep would re-yank the viewport every tick.
  const hasData = !!data;
  useEffect(() => {
    if (!hasData || !tab || tab === "overview") return;
    document.getElementById(tab)?.scrollIntoView({ block: "start" });
  }, [hasData, tab]);

  const projectId = data?.project ?? projectIdParam;

  const updateMetadataMutation = useMutation({
    mutationFn: (req: { name?: string }) =>
      apiClient.capabilities
        .capabilitiesPartialUpdate({ id: capabilityId, patchedCapabilityRequest: req })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Update failed");
          }
          throw error;
        }),
    onError: (e) => notify.error(e, "Couldn't save changes"),
    onSuccess: () => invalidateCapabilityQueries(queryClient),
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.capabilities.capabilitiesDestroy({ id: capabilityId }),
    onError: (e) => notify.error(e, "Couldn't delete capability"),
    onSuccess: () => {
      notify.success("Capability deleted");
      invalidateCapabilityQueries(queryClient);
      setDeleteDialogOpen(false);
      navigate({ search: { projectId }, to: "/" });
    },
  });

  if (isLoading || error || !data) {
    // Same title and glyph as the loaded page, so the header never flashes empty.
    return (
      <PageShell
        header={
          <PageHeader
            icon={
              <Icon.capability
                aria-hidden
                className="size-6 shrink-0 [image-rendering:pixelated]"
              />
            }
            title="Capability"
          />
        }
        variant="scroll"
      >
        {isLoading ? (
          <LoadingState fullPage />
        ) : (
          <div className="space-y-4">
            <Alert variant="destructive">
              {(error as Error)?.message || "Capability not found"}
            </Alert>
            <Button asChild size="sm" variant="secondary">
              <Link aria-label="Back" search={(prev) => prev} to="..">
                <Icon.back />
              </Link>
            </Button>
          </div>
        )}
      </PageShell>
    );
  }

  const capability = data;

  return (
    <PageShell
      header={
        <PageHeader
          actions={
            <Button
              onClick={guard(() =>
                navigate({
                  search: (prev) => ({
                    ...prev,
                    capability: capabilityId,
                    projectId,
                    view: "roots",
                  }),
                  to: "/observability",
                })
              )}
              size="sm"
              variant="secondary"
            >
              <Icon.observability />
              View traces
            </Button>
          }
          icon={
            <Icon.capability aria-hidden className="size-6 shrink-0 [image-rendering:pixelated]" />
          }
          meta={
            <>
              <CopyIdButton label="Copy capability id" value={capability.id} />
              {projectId ? <CopyIdButton label="Copy project id" value={projectId} /> : null}
              <span className="truncate font-mono text-xs" title={capability.slug}>
                {capability.slug}
              </span>
              {capability.status === "leftover" ? (
                <Badge variant="warning">Not in latest scan</Badge>
              ) : null}
              {capability.observed ? <Badge variant="neutral">Observed</Badge> : null}
            </>
          }
          title={
            <CapabilityNameEditor
              initialName={capability.name}
              isSaving={updateMetadataMutation.isPending}
              onSave={(name) => updateMetadataMutation.mutate({ name })}
            />
          }
        />
      }
      variant="scroll"
    >
      <DismissibleAlert
        error={updateMetadataMutation.isError ? (updateMetadataMutation.error as Error) : null}
        fallback="Failed to update capability"
        variant="warning"
      />
      <div className="space-y-4">
        <CapabilityFactsBar capability={capability} />
        <CapabilityPromptCard flow={capability.flow} />
        <TrajectoryFlowTab
          capability={capability}
          expanded={flowFull === true}
          onExpandedChange={setFlowFull}
        />
        <ComponentsExplorer flow={capability.flow} />
      </div>

      <hr className="border-border/60" />

      <section
        aria-labelledby="capability-dataset-heading"
        className="scroll-mt-6 space-y-4"
        id="dataset"
      >
        <h2 className={TITLE.section} id="capability-dataset-heading">
          Dataset
        </h2>
        <DatasetTab capabilityId={capabilityId} projectId={projectId} />
      </section>

      {featureFlags.evaluations && (
        <>
          <hr className="border-border/60" />
          <section
            aria-labelledby="capability-evaluators-heading"
            className="scroll-mt-6 space-y-4"
            id="evaluators"
          >
            <h2 className={TITLE.section} id="capability-evaluators-heading">
              Eval metrics
            </h2>
            <EvaluatorsTab
              capability={capability}
              capabilityId={capabilityId}
              projectId={projectId}
            />
          </section>
        </>
      )}

      <hr className="border-border/60" />

      <section
        aria-labelledby="capability-models-heading"
        className="scroll-mt-6 space-y-4"
        id="models"
      >
        <h2 className={TITLE.section} id="capability-models-heading">
          Models
        </h2>
        <ModelsTab capability={capability} capabilityId={capabilityId} projectId={projectId} />
      </section>

      <hr className="border-border/60" />

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3">
        <div className="min-w-0 flex-1 space-y-0.5 text-destructive">
          <p className="text-sm font-semibold">Danger zone</p>
          <p className={`${PROSE} text-sm leading-snug`}>
            Delete this capability. It leaves the agent; its traces, datasets, and runs stay.
          </p>
        </div>
        <Button
          className="shrink-0"
          disabled={deleteMutation.isPending}
          onClick={guard(() => setDeleteDialogOpen(true))}
          type="button"
          variant="destructive"
        >
          <Icon.delete />
          Delete capability
        </Button>
      </div>

      <ConfirmDialog
        confirmLabel="Delete capability"
        description="The capability leaves the agent. Its traces, datasets, and runs stay."
        destructive
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
        onOpenChange={setDeleteDialogOpen}
        open={deleteDialogOpen}
        title={`Delete ${capability.name}?`}
      />

      {isGuest ? null : (
        <CapabilityNextStepNudge capabilityId={capabilityId} projectId={projectId} />
      )}
    </PageShell>
  );
}
