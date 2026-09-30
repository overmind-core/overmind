import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";

import api from "@/client";
import { CONNECTORS, ConnectorDialog, type ConnectorMeta } from "@/components/connectors";
import { ConnectorSetupWizard } from "@/components/connectors/setup-wizard";
import { ConnectorReviewDialog } from "@/components/connectors/trace-groups";
import { CreateProjectDialog } from "@/components/create-project";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
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
import { formatDuration } from "@/lib/formatters";
import { ResolvedStatusBadge, resolveStatus } from "@/lib/job-status";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import { projectIdSearchSchema } from "@/lib/schemas";
import { PROSE } from "@/lib/typography";
import type { ConnectorCredential, ConnectorSyncRun } from "@/openapi";

const FAST_POLL_AFTER_SYNC_MS = 45_000;

export const Route = createFileRoute("/_auth/observability_/integrations")({
  component: IntegrationsPage,
  validateSearch: projectIdSearchSchema,
});

interface CredentialItemProps {
  meta: ConnectorMeta;
  credential: ConnectorCredential;
  projectId: string;
  /** Enter the 2s credentials refetch window (live Sync now stays `live` on the server). */
  onSyncKicked?: () => void;
  onAdd: () => void;
}

function ImportProgress({ credential }: { credential: ConnectorCredential }) {
  const total = credential.backfillTotal;
  const imported = credential.backfillImported;
  const progress = total ? Math.min(99, (imported / total) * 100) : null;
  const remaining = credential.importRemainingSeconds;
  return (
    <div className="flex max-w-full flex-col items-end gap-1.5" role="status">
      <ResolvedStatusBadge
        cfg={{ ...resolveStatus("running", "running"), label: "Importing" }}
        progress={progress}
        solidProgress
      />
      <span className="text-xs text-muted-foreground tabular-nums">
        {imported.toLocaleString()}
        {total != null ? ` / ${total.toLocaleString()}` : ""} traces
        {remaining != null
          ? ` · ~${formatDuration(Math.max(1000, remaining * 1000))} left`
          : total != null && imported >= total
            ? " · Finishing import…"
            : " · Estimating time…"}
      </span>
    </div>
  );
}

function CredentialItem({ meta, credential, projectId, onSyncKicked, onAdd }: CredentialItemProps) {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [showRuns, setShowRuns] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const importing = credential.syncStatus === "backfilling";
  const pending = credential.reviewSummary?.pendingGroups ?? 0;

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

  const sourceId = credential.activeConfig?.sourceProjectId;
  const sourceProjects = useQuery({
    enabled: meta.available && !!sourceId,
    queryFn: () =>
      api.connectorCredentials.connectorCredentialsSourceProjectsRetrieve({ id: credential.id }),
    queryKey: ["connector-source-projects", credential.id, credential.updatedAt?.getTime()],
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
  const sourceName =
    sourceProjects.data?.projects?.find((project) => project.id === sourceId)?.name ?? sourceId;

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
  const runs: ConnectorSyncRun[] = runsQuery.data?.results ?? [];

  return (
    <>
      <Card className="flex flex-col gap-3 px-4 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          {meta.logoUrl && <img alt="" className="size-9 shrink-0 rounded-md" src={meta.logoUrl} />}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <span className="text-sm font-semibold text-foreground">{meta.label}</span>
            <span className="text-xs text-muted-foreground">{credential.name}</span>
            {sourceId && (
              <p className="break-words text-xs text-muted-foreground">
                {meta.type === "galileo" ? "Source log stream" : "Source project"}: {sourceName}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {credential.totalTracesImported.toLocaleString()} traces imported
              {pending > 0 && !importing && (
                <span className="text-warning">
                  {" "}
                  · {pending.toLocaleString()} {pending === 1 ? "pattern needs" : "patterns need"}{" "}
                  review
                </span>
              )}
            </p>
          </div>
          {importing ? (
            <ImportProgress credential={credential} />
          ) : pending > 0 ? (
            <Button onClick={() => setReviewOpen(true)} size="sm">
              Review
            </Button>
          ) : credential.syncStatus === "live" ? (
            <Badge variant="success">
              <Icon.success className="size-3.5" />
              Completed
            </Badge>
          ) : null}
        </div>
        {credential.syncError && (
          <p className="text-xs text-destructive" role="alert">
            {credential.syncError}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
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
                disabled={syncMutation.isPending || importing}
                onClick={() => syncMutation.mutate()}
                size="sm"
                variant="secondary"
              >
                <Icon.refresh />
                Sync now
              </Button>
            )}
            <Button onClick={onAdd} size="sm" variant="secondary">
              New connection
            </Button>
            <Button onClick={() => setEditOpen(true)} size="sm" variant="secondary">
              Credentials
            </Button>
            <Button
              disabled={importing}
              onClick={() => setWizardOpen(true)}
              size="sm"
              variant="secondary"
            >
              <Icon.edit />
              Import traces
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
      </Card>

      <ConnectorReviewDialog
        credentialId={credential.id}
        name={credential.name}
        onClose={() => setReviewOpen(false)}
        open={reviewOpen}
        projectId={projectId}
      />
      <ConnectorDialog
        existing={credential}
        meta={meta}
        onClose={() => setEditOpen(false)}
        open={editOpen}
        projectId={projectId}
      />
      {meta.available ? (
        <ConnectorSetupWizard
          existing={credential}
          meta={meta}
          onClose={() => setWizardOpen(false)}
          onCompleted={() => onSyncKicked?.()}
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
  /** Called when the user wants to add a connection but has no project yet. */
  onRequireProject: () => void;
  onSyncKicked?: () => void;
}

function ConnectorSection({
  meta,
  credentials,
  projectId,
  onRequireProject,
  onSyncKicked,
}: ConnectorSectionProps) {
  const [addOpen, setAddOpen] = useState(false);
  return (
    <>
      {credentials.length ? (
        credentials.map((credential) => (
          <CredentialItem
            credential={credential}
            key={credential.id}
            meta={meta}
            onAdd={() => setAddOpen(true)}
            onSyncKicked={onSyncKicked}
            projectId={projectId}
          />
        ))
      ) : (
        <Card className="flex flex-wrap items-start gap-4 p-4">
          {meta.logoUrl && <img alt="" className="size-9 shrink-0 rounded-md" src={meta.logoUrl} />}
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{meta.label}</p>
            <p className={`${PROSE} mt-1 text-sm text-muted-foreground`}>{meta.description}</p>
          </div>
          {meta.available ? (
            <Button
              onClick={() => (projectId ? setAddOpen(true) : onRequireProject())}
              size="sm"
              variant="secondary"
            >
              Connect
            </Button>
          ) : (
            <Badge variant="secondary">Coming soon</Badge>
          )}
        </Card>
      )}
      {projectId && (
        <ConnectorSetupWizard
          meta={meta}
          onClose={() => setAddOpen(false)}
          onCompleted={() => onSyncKicked?.()}
          open={addOpen}
          projectId={projectId}
        />
      )}
    </>
  );
}

function IntegrationsPage() {
  const routeSearch = Route.useSearch();
  const authSearch = useSearch({ from: "/_auth" });
  const navigate = Route.useNavigate();
  const projectId = routeSearch.projectId ?? authSearch.projectId ?? "";
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [fastPollUntil, setFastPollUntil] = useState(0);
  const bumpFastPoll = () => setFastPollUntil(Date.now() + FAST_POLL_AFTER_SYNC_MS);
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
          description="Import traces from your provider, then review their capability assignments."
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

      <div className="flex flex-col gap-4">
        {CONNECTORS.map((meta) => (
          <ConnectorSection
            credentials={credentialsByType[meta.type] ?? []}
            key={meta.type}
            meta={meta}
            onRequireProject={() => setCreateProjectOpen(true)}
            onSyncKicked={bumpFastPoll}
            projectId={projectId}
          />
        ))}
      </div>

      <CreateProjectDialog onOpenChange={setCreateProjectOpen} open={createProjectOpen} />
    </PageShell>
  );
}
