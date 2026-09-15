import { type ReactNode, useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";

import api from "@/client";
import { CONNECTORS, ConnectorDialog, type ConnectorMeta } from "@/components/connectors";
import { ConnectorSetupWizard } from "@/components/connectors/setup-wizard";
import { CreateProjectDialog } from "@/components/create-project";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DateTime } from "@/components/ui/datetime";
import { FloatingNudge } from "@/components/ui/floating-nudge";
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useNudgeDismissalCount } from "@/hooks/use-nudge-dismissal";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import { projectIdSearchSchema } from "@/lib/schemas";
import { PROSE } from "@/lib/typography";
import type { ConnectorCredential, ConnectorSyncRun } from "@/openapi";

type SyncStartedInfo = {
  credentialId: string;
  targetProjectId: string;
  connectorType: string;
  /** Epoch ms — bumps dismiss gate so a later sync can re-show the nudge. */
  at: number;
};

const SYNC_NUDGE_KEY = (credentialId: string) => `connector-sync-nudge:${credentialId}`;

/** Keep the credentials list on the 2s poll path after Sync now / start sync. */
const FAST_POLL_AFTER_SYNC_MS = 45_000;

export const Route = createFileRoute("/_auth/observability_/integrations")({
  component: IntegrationsPage,
  validateSearch: projectIdSearchSchema,
});

interface CredentialItemProps {
  meta: ConnectorMeta;
  credential: ConnectorCredential;
  projectId: string;
  onSyncStarted?: (info: SyncStartedInfo) => void;
  /** Enter the 2s credentials refetch window (live Sync now stays `live` on the server). */
  onSyncKicked?: () => void;
}

function SyncStatusLine({ credential }: { credential: ConnectorCredential }) {
  const { syncStatus, backfillImported, backfillTotal, syncError, lastSyncedAt, nextPollAt } =
    credential;

  if (!credential.autoSyncEnabled && syncStatus !== "backfilling" && syncStatus !== "live") {
    return <span className="text-xs text-muted-foreground">Auto-sync off</span>;
  }

  if (syncStatus === "backfilling") {
    // Prefer totalTracesImported — same unit as the Traces stat.
    // No Spinner: lookback walks many (often empty) windows after the count
    // stops moving; a spinner reads as "still importing" when history scan
    // is just draining older days.
    const tracesDone = credential.totalTracesImported || backfillImported;
    const pct =
      backfillTotal && backfillTotal > 0
        ? Math.round((tracesDone / backfillTotal) * 100)
        : undefined;
    return (
      <div className="flex min-w-[180px] flex-col gap-1">
        <span className="text-xs text-muted-foreground">
          Importing history · {tracesDone.toLocaleString()}
          {backfillTotal ? ` / ~${backfillTotal.toLocaleString()}` : ""} traces
        </span>
        {pct != null && <Progress label="Backfill progress" percent={pct} />}
      </div>
    );
  }
  if (syncStatus === "error") {
    return (
      <span className="flex items-start gap-1.5 text-xs text-destructive">
        <Icon.warning className="mt-0.5 size-3.5 shrink-0" />
        <span>{syncError || "Sync error — retrying automatically"}</span>
      </span>
    );
  }
  if (syncStatus === "live") {
    return (
      <span className="text-xs text-muted-foreground">
        <span className="mr-1 inline-block size-1.5 rounded-xs bg-success align-middle" />
        Live
        {lastSyncedAt && (
          <>
            {" "}
            · last <DateTime value={lastSyncedAt} />
          </>
        )}
        {credential.autoSyncEnabled && nextPollAt && (
          <>
            {" "}
            · next <DateTime value={nextPollAt} />
          </>
        )}
      </span>
    );
  }
  return <span className="text-xs text-muted-foreground">Waiting for first sync…</span>;
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-sm font-medium text-foreground">{value}</div>
    </div>
  );
}

function CredentialItem({
  meta,
  credential,
  projectId,
  onSyncStarted,
  onSyncKicked,
}: CredentialItemProps) {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [showRuns, setShowRuns] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });

  const runsQuery = useQuery({
    enabled: showRuns && meta.available,
    queryFn: () =>
      api.connectorCredentials.connectorCredentialsRunsList({
        id: credential.id,
        pageSize: 10,
      }),
    queryKey: ["connector-sync-runs", credential.id],
  });

  const disconnectMutation = useMutation({
    mutationFn: () => api.connectorCredentials.connectorCredentialsDestroy({ id: credential.id }),
    onError: (e) => notify.error(e, "Couldn't disconnect"),
    onSuccess: () => {
      toast.success("Disconnected");
      invalidate();
    },
  });

  const markBackfillingOptimistic = () => {
    // Server only sets backfilling when cursor.mode != live. For history syncs,
    // flip immediately so "Importing history" shows before the next list refetch.
    if (credential.syncStatus === "live") return;
    qc.setQueryData(
      ["connector-credentials", projectId],
      (prev: { results?: ConnectorCredential[] } | undefined) => {
        if (!prev?.results) return prev;
        return {
          ...prev,
          results: prev.results.map((c) =>
            c.id === credential.id ? { ...c, syncError: "", syncStatus: "backfilling" as const } : c
          ),
        };
      }
    );
  };

  const syncMutation = useMutation({
    mutationFn: () =>
      api.connectorCredentials.connectorCredentialsSyncCreate({ id: credential.id }),
    onError: (e: Error) => toast.error(e.message),
    onSuccess: () => {
      toast.success("Sync queued");
      onSyncKicked?.();
      markBackfillingOptimistic();
      invalidate();
    },
  });

  const autoSyncMutation = useMutation({
    mutationFn: async (autoSyncEnabled: boolean) => {
      const updated = await api.connectorCredentials.connectorCredentialsPartialUpdate({
        id: credential.id,
        patchedConnectorCredentialRequest: { autoSyncEnabled },
      });
      // Turning auto-sync on starts a poll; the patch itself does not.
      if (autoSyncEnabled) {
        await api.connectorCredentials.connectorCredentialsSyncCreate({ id: credential.id });
      }
      return updated;
    },
    onError: (e: Error) => toast.error(e.message),
    onSuccess: (updated) => {
      toast.success(
        updated.autoSyncEnabled
          ? "Auto-sync on — polling started"
          : "Auto-sync off — automatic polling stopped"
      );
      if (updated.autoSyncEnabled) {
        onSyncKicked?.();
        qc.setQueryData(
          ["connector-credentials", projectId],
          (prev: { results?: ConnectorCredential[] } | undefined) => {
            if (!prev?.results) return prev;
            return {
              ...prev,
              results: prev.results.map((c) =>
                c.id === credential.id
                  ? {
                      ...c,
                      autoSyncEnabled: true,
                      syncError: "",
                      syncStatus: c.syncStatus === "live" ? c.syncStatus : ("backfilling" as const),
                    }
                  : c
              ),
            };
          }
        );
      }
      invalidate();
    },
  });

  const autoSyncId = `auto-sync-${credential.id}`;
  const mappedCapabilities =
    credential.capabilityMapping?.assignments &&
    typeof credential.capabilityMapping.assignments === "object"
      ? Object.keys(credential.capabilityMapping.assignments as object).length
      : 0;
  const runs: ConnectorSyncRun[] = runsQuery.data?.results ?? [];
  const openConfig = () => {
    if (meta.available) setWizardOpen(true);
    else setEditOpen(true);
  };

  return (
    <>
      <div className="flex flex-col gap-3 rounded-md border border-border bg-wash-subtle px-3 py-3">
        <div className="flex items-start gap-3">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="text-sm font-medium text-foreground">{credential.name}</span>
            <SyncStatusLine credential={credential} />
          </div>

          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            {meta.available && (
              <div className="mr-1 flex items-center gap-2">
                <Switch
                  aria-label="Enable automatic polling and historical backfill"
                  checked={credential.autoSyncEnabled}
                  disabled={autoSyncMutation.isPending}
                  id={autoSyncId}
                  onCheckedChange={(checked) => autoSyncMutation.mutate(checked)}
                />
                <Label
                  className="cursor-pointer text-xs text-muted-foreground"
                  htmlFor={autoSyncId}
                >
                  Auto-sync
                </Label>
              </div>
            )}
            {meta.available && (
              <Button
                className="gap-1.5"
                disabled={syncMutation.isPending}
                onClick={() => syncMutation.mutate()}
                size="sm"
                variant="secondary"
              >
                <Icon.refresh />
                Sync now
              </Button>
            )}
            <Button onClick={openConfig} size="sm" variant="secondary">
              <Icon.edit />
              Edit
            </Button>
            <ConfirmDialog
              confirmLabel="Disconnect"
              description={`Stops syncing from ${meta.label}. Imported traces stay in Overmind. Reconnecting with the same API key resumes this connection.`}
              destructive
              isPending={disconnectMutation.isPending}
              keepOpenOnError
              onConfirm={() => disconnectMutation.mutateAsync()}
              title="Disconnect?"
              trigger={
                <Button
                  aria-label="Disconnect"
                  className="text-destructive hover:text-destructive"
                  size="sm"
                  variant="secondary"
                >
                  <Icon.stop />
                </Button>
              }
            />
          </div>
        </div>

        {meta.available && (
          <>
            <div className="grid grid-cols-3 gap-3 border-t border-border/70 pt-3">
              <Stat label="Traces" value={(credential.totalTracesImported ?? 0).toLocaleString()} />
              <Stat label="Spans" value={(credential.totalSpansImported ?? 0).toLocaleString()} />
              <Stat label="Mapped capabilities" value={mappedCapabilities.toLocaleString()} />
            </div>

            <div className="flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
              <Button asChild size="sm" variant="secondary">
                <Link
                  search={{ projectId, service_name__icontains: meta.type }}
                  to="/observability"
                >
                  <Icon.observability />
                  View traces
                </Link>
              </Button>
              <Button onClick={() => setShowRuns((v) => !v)} size="sm" variant="secondary">
                <Icon.history />
                {showRuns ? "Hide sync history" : "Sync history"}
              </Button>
            </div>

            {showRuns && (
              <div className="overflow-hidden rounded-md border border-border/70">
                {runsQuery.isLoading ? (
                  <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                    <Spinner size="sm" /> Loading runs…
                  </div>
                ) : runs.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-muted-foreground">No sync runs yet.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Started</TableHead>
                        <TableHead>Mode</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="text-right">Traces</TableHead>
                        <TableHead className="text-right">Spans</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {runs.map((run) => (
                        <TableRow key={run.id}>
                          <TableCell className="text-xs">
                            {run.startedAt ? <DateTime value={run.startedAt} /> : "—"}
                          </TableCell>
                          <TableCell className="text-xs">{run.mode}</TableCell>
                          <TableCell className="text-xs">
                            {run.status}
                            {run.error ? (
                              <span className="ml-1 text-destructive">{run.error}</span>
                            ) : null}
                          </TableCell>
                          <TableCell className="text-right text-xs">
                            {run.tracesSeen.toLocaleString()}
                          </TableCell>
                          <TableCell className="text-right text-xs">
                            {run.spansCreated.toLocaleString()}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {meta.available ? (
        <ConnectorSetupWizard
          existing={credential}
          meta={meta}
          onClose={() => setWizardOpen(false)}
          onCompleted={({ credentialId, targetProjectId }) => {
            onSyncKicked?.();
            onSyncStarted?.({
              at: Date.now(),
              connectorType: meta.type,
              credentialId,
              targetProjectId,
            });
          }}
          open={wizardOpen}
          projectId={projectId}
        />
      ) : (
        <ConnectorDialog
          existing={credential}
          meta={meta}
          onClose={() => setEditOpen(false)}
          open={editOpen}
          projectId={projectId}
        />
      )}
    </>
  );
}

interface ConnectorSectionProps {
  meta: ConnectorMeta;
  credentials: ConnectorCredential[];
  projectId: string;
  isLast: boolean;
  /** Called when the user wants to add a connection but has no project yet. */
  onRequireProject: () => void;
  onSyncStarted?: (info: SyncStartedInfo) => void;
  onSyncKicked?: () => void;
}

function ConnectorSection({
  meta,
  credentials,
  projectId,
  isLast,
  onRequireProject,
  onSyncStarted,
  onSyncKicked,
}: ConnectorSectionProps) {
  const [addOpen, setAddOpen] = useState(false);

  return (
    <>
      <div className="py-5">
        <div className="flex items-start gap-4">
          {meta.logoUrl && (
            <img
              alt={meta.label}
              className="mt-0.5 size-9 shrink-0 rounded-md"
              src={meta.logoUrl}
            />
          )}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center gap-2.5">
              <span className="text-sm font-semibold text-foreground">{meta.label}</span>
              {credentials.length > 0 && (
                <Badge className="gap-1 px-1.5 py-0 text-xs" variant="outline">
                  <span className="size-1.5 rounded-xs bg-success" />
                  {credentials.length === 1 ? "1 connection" : `${credentials.length} connections`}
                </Badge>
              )}
              {!meta.available && (
                <Badge className="px-1.5 py-0 text-xs" variant="secondary">
                  Coming soon
                </Badge>
              )}
            </div>
            <p className={`${PROSE} text-sm leading-relaxed text-muted-foreground`}>
              {meta.description}
            </p>
          </div>

          {meta.available && (
            <Button
              className="shrink-0"
              onClick={() => (projectId ? setAddOpen(true) : onRequireProject())}
              size="sm"
              variant="secondary"
            >
              <Icon.add />
              New connection
            </Button>
          )}
        </div>

        {credentials.length > 0 && (
          <div className="mt-3 flex flex-col gap-2">
            {credentials.map((cred) => (
              <CredentialItem
                credential={cred}
                key={cred.id}
                meta={meta}
                onSyncKicked={onSyncKicked}
                onSyncStarted={onSyncStarted}
                projectId={projectId}
              />
            ))}
          </div>
        )}
      </div>

      {!isLast && <Separator />}

      {/* Available connectors use the setup wizard; others fall back to the credentials dialog. */}
      {projectId &&
        (meta.available ? (
          <ConnectorSetupWizard
            meta={meta}
            onClose={() => setAddOpen(false)}
            onCompleted={({ credentialId, targetProjectId }) => {
              onSyncKicked?.();
              onSyncStarted?.({
                at: Date.now(),
                connectorType: meta.type,
                credentialId,
                targetProjectId,
              });
            }}
            open={addOpen}
            projectId={projectId}
          />
        ) : (
          <ConnectorDialog
            meta={meta}
            onClose={() => setAddOpen(false)}
            open={addOpen}
            projectId={projectId}
          />
        ))}
    </>
  );
}

/** Moved out of Settings: connectors feed traces, so they live with the
    Observability surface they fill. */
function IntegrationsPage() {
  const routeSearch = Route.useSearch();
  const authSearch = useSearch({ from: "/_auth" });
  const navigate = Route.useNavigate();
  const projectId = routeSearch.projectId ?? authSearch.projectId ?? "";
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [postSync, setPostSync] = useState<SyncStartedInfo | null>(null);
  const [fastPollUntil, setFastPollUntil] = useState(0);
  const bumpFastPoll = () => setFastPollUntil(Date.now() + FAST_POLL_AFTER_SYNC_MS);
  const { dismissed: syncNudgeDismissed, dismiss: dismissSyncNudge } = useNudgeDismissalCount(
    postSync ? SYNC_NUDGE_KEY(postSync.credentialId) : "",
    postSync?.at ?? 0
  );

  const { createProject } = routeSearch;
  useEffect(() => {
    if (!createProject) return;
    setCreateProjectOpen(true);
    navigate({
      replace: true,
      search: (prev) => ({
        ...prev,
        createProject: undefined,
      }),
    });
  }, [createProject, navigate]);

  const credentialsQuery = useQuery({
    enabled: !!projectId,
    queryFn: () => api.connectorCredentials.connectorCredentialsList({ project: projectId }),
    queryKey: ["connector-credentials", projectId],
    // Idle cards: 30s is fine. During backfill/error — or right after Sync now on
    // a Live credential (server keeps sync_status=live) — poll every 2s so last/
    // next and counters move.
    refetchInterval: (q) =>
      backoffPolling(q, () => {
        const results = q.state.data?.results ?? [];
        const busy = results.some(
          (c) => c.syncStatus === "backfilling" || c.syncStatus === "error"
        );
        const kicked = Date.now() < fastPollUntil;
        return busy || kicked ? 2_000 : 30_000;
      }),
    refetchIntervalInBackground: true,
  });

  const credentials = credentialsQuery.data?.results ?? [];
  const credentialsByType = credentials.reduce<Record<string, ConnectorCredential[]>>((acc, c) => {
    const key = c.connectorType as string;
    // biome-ignore lint/suspicious/noAssignInExpressions: allowed
    (acc[key] ??= []).push(c);
    return acc;
  }, {});

  return (
    <PageShell
      header={
        <PageHeader
          actions={credentialsQuery.isLoading ? <Spinner /> : undefined}
          description="Connect third-party observability platforms to pull traces into Overmind automatically."
          icon={<Icon.integrations aria-hidden className="size-6 shrink-0" />}
          title="Integrations"
        />
      }
      variant="scroll"
    >
      {!projectId && (
        <div className="flex flex-col gap-3 rounded-md border border-border bg-wash-subtle px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">No project selected</p>
            <p className={`${PROSE} text-sm text-muted-foreground`}>
              Browse the available integrations below. Create a project — or pick one from the
              switcher — to save a connection.
            </p>
          </div>
          <Button className="shrink-0" onClick={() => setCreateProjectOpen(true)} size="sm">
            <Icon.add />
            New project
          </Button>
        </div>
      )}

      <div className="rounded-md border border-border">
        {CONNECTORS.map((meta, i) => (
          <div className="px-4" key={meta.type}>
            <ConnectorSection
              credentials={credentialsByType[meta.type] ?? []}
              isLast={i === CONNECTORS.length - 1}
              meta={meta}
              onRequireProject={() => setCreateProjectOpen(true)}
              onSyncKicked={bumpFastPoll}
              onSyncStarted={setPostSync}
              projectId={projectId}
            />
          </div>
        ))}
      </div>

      <CreateProjectDialog onOpenChange={setCreateProjectOpen} open={createProjectOpen} />

      {postSync && !syncNudgeDismissed ? (
        <FloatingNudge
          cta={
            <Button asChild size="sm">
              <Link
                search={{
                  projectId: postSync.targetProjectId,
                  service_name__icontains: postSync.connectorType,
                }}
                to="/observability"
              >
                <Icon.observability />
                View traces
              </Link>
            </Button>
          }
          description="Imported traces appear in Observability as the sync runs"
          icon={<Icon.observability className="size-5" />}
          onDismiss={() => {
            dismissSyncNudge();
            setPostSync(null);
          }}
          title="Sync started"
        />
      ) : null}
    </PageShell>
  );
}
