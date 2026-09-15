import { useEffect, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { z } from "zod";

import apiClient from "@/client";
import { CreateApiKeyDialog } from "@/components/create-api-key-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useOnboardingStatus, useProjectQuery } from "@/hooks/use-query";
import { useSubscriptionQuery } from "@/hooks/use-subscription";
import { useDeleteToken, useTokensList } from "@/hooks/use-tokens";
import { ApiError } from "@/lib/api-error";
import { featureFlags } from "@/lib/feature-flags";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { notify } from "@/lib/notify";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import { PlanEnum } from "@/openapi";

export const Route = createFileRoute("/_auth/projects/$projectId/")({
  component: ProjectDetailPage,
  validateSearch: z.object({
    // Deep-links from the command palette (⌘K "add member"): scroll straight to
    // the members card. `.catch` so retired section values fall back quietly.
    section: z.enum(["members"]).optional().catch(undefined),
  }),
});

function focusInviteInput() {
  const input = document.getElementById("add-user-email");
  const target = input ?? document.getElementById("members-heading");
  target?.scrollIntoView({ behavior: "smooth", block: "center" });
  if (input instanceof HTMLInputElement) input.focus({ preventScroll: true });
}

function ProjectDetailPage() {
  const { projectId } = Route.useParams();
  const { section } = Route.useSearch();
  const { data, isLoading, error } = useProjectQuery(projectId);

  // Keyed on data PRESENCE, not identity: the detail query polls, and a `data`
  // dep would re-yank the viewport every tick.
  const hasData = !!data;
  useEffect(() => {
    if (!hasData || section !== "members") return;
    focusInviteInput();
  }, [hasData, section]);

  const header = (
    <PageHeader
      description="Basic information, API keys, and members for this project."
      icon={<Icon.project aria-hidden className="size-6 shrink-0 [image-rendering:pixelated]" />}
      title={data?.name ?? "Project"}
    />
  );

  if (isLoading) {
    return (
      <PageShell header={header} variant="scroll">
        <div className="space-y-4">
          <Skeleton className="h-40 w-full rounded-md" />
          <Skeleton className="h-40 w-full rounded-md" />
        </div>
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell header={header} variant="scroll">
        <Alert variant="destructive">Failed to load project: {(error as Error).message}</Alert>
      </PageShell>
    );
  }

  if (!data) {
    return (
      <PageShell header={header} variant="scroll">
        <EmptyState
          description="It may have been deleted, or you may not have access to it."
          icon={Icon.project}
          title="Project not found"
        />
      </PageShell>
    );
  }

  return (
    <PageShell header={header} variant="scroll">
      <ProjectOverviewBand project={data} projectId={projectId} />
      <ProjectApiKeys projectId={projectId} />
      <hr className="border-border/60" />
      <ProjectMembersCard projectId={projectId} />
      <hr className="border-border/60" />
      <DeleteProjectCard projectId={projectId} />
    </PageShell>
  );
}

/** Shows a truncated id; the full one goes to the clipboard. */
function ProjectIdChip({ id }: { id: string }) {
  const { copied, copy } = useCopy(id);
  return (
    <button
      aria-label="Copy project id"
      className="flex h-6 items-center gap-1.5 rounded-sm font-mono text-sm text-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={copy}
      title={`Copy ${id}`}
      type="button"
    >
      {id.slice(0, 8)}…
      {copied ? (
        <Icon.success className="size-3.5 text-success" />
      ) : (
        <Icon.copy className="size-3.5 text-muted-foreground" />
      )}
    </button>
  );
}

function Fact({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 px-4 py-3", className)}>
      <p className="pixel-label mb-1.5 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function ContentStat({
  count,
  label,
  projectId,
  to,
}: {
  count: number | undefined;
  label: string;
  projectId: string;
  to: string;
}) {
  return (
    <Link
      className="group flex flex-col gap-1.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      search={{ projectId }}
      to={to}
    >
      <span className="pixel-label text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium leading-none tabular-nums text-foreground transition-colors group-hover:text-primary">
        {count ?? "—"}
      </span>
    </Link>
  );
}

/** Only the DRF `count` is wanted, so the page size stays minimal. */
function useProjectContentCounts(projectId: string) {
  const capabilities = useQuery({
    queryFn: () =>
      apiClient.capabilities.capabilitiesList({ page: 1, pageSize: 1, project: projectId }),
    queryKey: ["project-content-count", "capabilities", projectId],
    select: (d) => d.count,
  });
  const datasets = useQuery({
    queryFn: () => apiClient.datasets.datasetsList({ page: 1, pageSize: 1, project: projectId }),
    queryKey: ["project-content-count", "datasets", projectId],
    select: (d) => d.count,
  });
  const evalRuns = useQuery({
    queryFn: () => apiClient.evalRuns.evalRunsList({ page: 1, pageSize: 1, project: projectId }),
    queryKey: ["project-content-count", "eval-runs", projectId],
    select: (d) => d.count,
  });
  const models = useQuery({
    queryFn: () =>
      apiClient.deployedModels.deployedModelsList({ page: 1, pageSize: 5, project: projectId }),
    queryKey: ["project-content-count", "models", projectId],
    select: (d) => d.count,
  });
  return {
    capabilities: capabilities.data,
    datasets: datasets.data,
    evalRuns: evalRuns.data,
    models: models.data,
  };
}

function ProjectOverviewBand({
  project,
  projectId,
}: {
  project: NonNullable<ReturnType<typeof useProjectQuery>["data"]>;
  projectId: string;
}) {
  const counts = useProjectContentCounts(projectId);

  return (
    <div className="rounded-md border border-border bg-card">
      <div className="flex flex-col divide-y divide-border/70 sm:flex-row sm:divide-x sm:divide-y-0">
        <Fact className="sm:min-w-0 sm:flex-1" label="Source">
          <span className="flex h-6 items-center text-sm text-muted-foreground">SDK</span>
        </Fact>
        <Fact label="Project ID">
          <ProjectIdChip id={project.id} />
        </Fact>
        <Fact label="Slug">
          <span className="flex h-6 items-center truncate font-mono text-sm text-foreground">
            {project.slug}
          </span>
        </Fact>
        <Fact label="Created">
          <span className="flex h-6 items-center text-sm text-foreground">
            <DateTime value={project.createdAt} />
          </span>
        </Fact>
      </div>
      <div className="flex flex-wrap items-start gap-x-10 gap-y-4 border-t border-border/70 px-4 py-3">
        <ContentStat
          count={counts.capabilities}
          label="Capabilities"
          projectId={projectId}
          to="/"
        />
        <ContentStat
          count={counts.datasets}
          label="Datasets"
          projectId={projectId}
          to="/datasets"
        />
        <ContentStat
          count={counts.evalRuns}
          label="Eval runs"
          projectId={projectId}
          to="/evaluations"
        />
        <ContentStat count={counts.models} label="Models" projectId={projectId} to="/inference" />
      </div>
    </div>
  );
}

function ApiKeysList({ projectId }: { projectId: string }) {
  const { data: tokenData, isLoading, error } = useTokensList(projectId);
  const deleteToken = useDeleteToken();

  if (isLoading) {
    return (
      <div className="overflow-hidden rounded-md border border-border">
        {[1, 2, 3].map((i) => (
          <Skeleton
            className="h-10 w-full rounded-none border-b border-border/70 last:border-0"
            key={i}
          />
        ))}
      </div>
    );
  }

  if (error) {
    return <Alert variant="destructive">Failed to load API keys: {(error as Error).message}</Alert>;
  }

  if (!tokenData || tokenData.tokens.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border py-12 text-center">
        <Icon.lock className="mb-3 size-8 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">No API keys for this project yet.</p>
        <p className="mt-1 text-xs text-muted-foreground">Create one using the button above.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <Table className="w-full [&_td:first-child]:pl-4 [&_th:first-child]:pl-4">
        <TableHeader>
          <TableRow>
            <TableHead className="h-8 py-1">Name</TableHead>
            <TableHead className="h-8 py-1 text-center">Prefix</TableHead>
            <TableHead className="hidden h-8 py-1 text-center sm:table-cell">Created</TableHead>
            <TableHead className="h-8 py-1 text-center">Status</TableHead>
            <TableHead className="h-8 w-16 py-1 text-center">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tokenData.tokens.map((token) => (
            <TableRow className="hover:bg-wash-raised" key={token.tokenId}>
              <TableCell className="py-1.5 font-mono text-sm">{token.name ?? "Unnamed"}</TableCell>
              <TableCell className="py-1.5 text-center font-mono text-xs text-muted-foreground">
                {token.prefix}…
              </TableCell>
              <TableCell className="hidden py-1.5 text-center text-sm text-muted-foreground sm:table-cell">
                <DateTime value={token.createdAt} />
              </TableCell>
              <TableCell className="py-1.5 text-center">
                <Badge size="chip" variant={token.isActive === false ? "secondary" : "success"}>
                  {token.isActive === false ? "Inactive" : "Active"}
                </Badge>
              </TableCell>
              <TableCell className="py-1.5 text-center">
                <ConfirmDialog
                  confirmLabel="Delete key"
                  description="This API key will stop working immediately."
                  destructive
                  isPending={deleteToken.isPending}
                  onConfirm={() => deleteToken.mutate(token.tokenId)}
                  title="Delete this API key?"
                  trigger={
                    <Button
                      aria-label="Delete API key"
                      className="text-destructive hover:text-destructive"
                      disabled={deleteToken.isPending}
                      size="icon-xs"
                      variant="ghost"
                    >
                      <Icon.delete />
                    </Button>
                  }
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ProjectApiKeys({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);

  const handleCreated = () => {
    queryClient.invalidateQueries({ queryKey: ["tokens", projectId] });
  };

  return (
    <>
      <section aria-labelledby="api-keys-heading" className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className={TITLE.section} id="api-keys-heading">
              API keys
            </h2>
            <p className={cn(PROSE, "mt-1 text-sm text-muted-foreground")}>
              {featureFlags.mcp ? (
                <>
                  The full key is shown once, at creation. Use it for the API and for MCP in any
                  coding agent (<code className="font-mono">/api/mcp/</code>).
                </>
              ) : (
                "The full key is shown once, at creation."
              )}
            </p>
          </div>
          <Button onClick={() => setShowCreateModal(true)} size="sm">
            <Icon.add />
            New API key
          </Button>
        </div>
        <ApiKeysList projectId={projectId} />
      </section>

      <CreateApiKeyDialog
        onCreated={handleCreated}
        onOpenChange={setShowCreateModal}
        open={showCreateModal}
        projectId={projectId}
      />
    </>
  );
}

const SELECTED_PROJECT_KEY = "__selectedProject";

function DeleteProjectCard({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { projectId: activeProjectId } = useSearch({ from: "/_auth" });
  const { data: project } = useProjectQuery(projectId);

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.projects.projectsDestroy({ id: projectId }),
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete project.");
    },
    onSuccess: () => {
      toast.success("Project deleted.");
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (localStorage.getItem(SELECTED_PROJECT_KEY) === projectId) {
        localStorage.removeItem(SELECTED_PROJECT_KEY);
      }
      if (activeProjectId === projectId) {
        navigate({ search: {}, to: "/projects" });
      } else {
        navigate({ to: "/projects" });
      }
    },
  });

  const projectName = project?.name ?? "this project";

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3">
      <div className="min-w-0 flex-1 space-y-0.5 text-destructive">
        <p className="text-sm font-semibold">Danger zone</p>
        <p className={`${PROSE} text-sm leading-snug`}>
          Permanently delete this project and all associated data (capabilities, datasets, traces,
          API keys).
        </p>
      </div>
      <ConfirmDialog
        confirmLabel="Delete project"
        description="This cannot be undone. All resources in this project will be removed permanently."
        destructive
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
        title={`Delete ${projectName}?`}
        trigger={
          <Button
            className="shrink-0"
            disabled={deleteMutation.isPending}
            type="button"
            variant="destructive"
          >
            <Icon.skull />
            Delete project
          </Button>
        }
      />
    </div>
  );
}

class MembershipInviteError extends Error {
  constructor(
    message: string,
    readonly code: "user_not_found" | "other",
    readonly email: string
  ) {
    super(message);
    this.name = "MembershipInviteError";
  }
}

function parseInviteMembershipError(err: unknown, email: string): MembershipInviteError {
  if (err instanceof MembershipInviteError) return err;
  // The client middleware rethrows every non-2xx response as ApiError; the
  // generated ResponseError never reaches call sites.
  if (err instanceof ApiError) {
    const isNotFound =
      err.code === "user_not_found" || err.message.toLowerCase().includes("no user found");
    return new MembershipInviteError(err.message, isNotFound ? "user_not_found" : "other", email);
  }
  return new MembershipInviteError(
    err instanceof Error ? err.message : "Failed to add member.",
    "other",
    email
  );
}

function ProjectMembersCard({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [emailInput, setEmailInput] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const isValidEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailInput.trim());
  const subscriptionQuery = useSubscriptionQuery();
  const meQuery = useOnboardingStatus();
  const myUserId = meQuery.data?.id;
  const isPro = subscriptionQuery.data?.plan === PlanEnum.pro;

  const {
    data: membersData,
    isLoading,
    error,
  } = useQuery({
    queryFn: () => apiClient.projects.projectsMembershipsList({ projectId }),
    queryKey: ["project-members", projectId],
  });

  const members = membersData?.results;
  const canRemoveOthers = !!members && myUserId != null && members.some((m) => m.user !== myUserId);

  const { data: invitesData } = useQuery({
    queryFn: () => apiClient.projects.projectsInvitesList({ projectId }),
    queryKey: ["project-invites", projectId],
  });
  const invites = invitesData?.results ?? [];
  const showActions = canRemoveOthers || invites.length > 0;

  const inviteMember = useMutation({
    mutationFn: async (email: string) => {
      try {
        return await apiClient.projects.projectsMembershipsCreate({
          projectId,
          projectMembershipCreateRequest: { email },
        });
      } catch (err) {
        throw parseInviteMembershipError(err, email);
      }
    },
    onSuccess: () => {
      setEmailInput("");
      queryClient.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });

  // Second step behind an explicit CTA: sends the Clerk sign-up invitation for
  // an email the membership endpoint rejected as unregistered.
  const sendInvite = useMutation({
    mutationFn: async (email: string) => {
      try {
        await apiClient.projects.projectsInvitesCreate({
          projectId,
          projectInviteCreateRequest: { email },
        });
        return email;
      } catch (err) {
        throw parseInviteMembershipError(err, email);
      }
    },
    onError: (e) => notify.error(e, "Couldn't send invitation"),
    onSuccess: (email) => {
      setEmailInput("");
      setSentTo(email);
      inviteMember.reset();
      queryClient.invalidateQueries({ queryKey: ["project-invites", projectId] });
    },
  });

  const revokeInvite = useMutation({
    mutationFn: (inviteId: string) =>
      apiClient.projects.projectsInvitesDestroy({ id: inviteId, projectId }),
    onError: (e) => notify.error(e, "Couldn't revoke invitation"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-invites", projectId] });
    },
  });

  const removeMember = useMutation({
    mutationFn: (membershipId: string) =>
      apiClient.projects.projectsMembershipsDestroy({
        id: membershipId,
        projectId,
      }),
    onError: (e) => notify.error(e, "Couldn't remove member"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
  });

  const inviteError =
    inviteMember.error instanceof MembershipInviteError ? inviteMember.error : null;

  return (
    <section aria-labelledby="members-heading" className="space-y-4">
      <div>
        <h2 className={TITLE.section} id="members-heading">
          Members
        </h2>
        <p className={cn(PROSE, "mt-1 text-sm text-muted-foreground")}>
          {isPro
            ? "Add members by email. Emails without an account get a sign-up invitation. Every member can access everything in this project."
            : "Free plan is one seat. Upgrade to Pro to invite teammates."}
        </p>
      </div>
      <div className="space-y-4">
        {isPro ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="add-user-email">Add by email</Label>
              <Input
                id="add-user-email"
                inputMode="email"
                onChange={(e) => {
                  setEmailInput(e.target.value);
                  setSentTo(null);
                  if (inviteMember.isError) inviteMember.reset();
                }}
                placeholder="user@example.com"
                type="email"
                value={emailInput}
              />
            </div>
            <Button
              className="gap-2"
              disabled={!isValidEmail || inviteMember.isPending}
              onClick={() => inviteMember.mutate(emailInput.trim())}
            >
              <Icon.addUser />
              Add member
            </Button>
          </div>
        ) : (
          <Button asChild className="gap-2" variant="outline">
            <a href={PUBLIC_PRICING_URL} rel="noreferrer" target="_blank">
              Upgrade to Pro
            </a>
          </Button>
        )}

        {inviteError?.code === "user_not_found" ? (
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-md border border-border bg-wash-raised px-4 py-3">
            <p className="text-sm text-foreground">
              <span className="font-mono">{inviteError.email}</span> is not registered yet.
            </p>
            <Button
              disabled={sendInvite.isPending}
              onClick={() => sendInvite.mutate(inviteError.email)}
              size="sm"
              type="button"
            >
              <Icon.addUser />
              Send invitation
            </Button>
          </div>
        ) : inviteMember.isError ? (
          <Alert variant="destructive">
            Failed to add member: {inviteError?.message ?? (inviteMember.error as Error).message}
          </Alert>
        ) : null}

        {sentTo ? (
          <div className="flex items-center gap-2 rounded-md border border-border bg-wash-raised px-4 py-3 text-sm text-foreground">
            <Icon.success className="size-4 shrink-0 text-success" />
            <p>
              Invitation sent. Ask <span className="font-mono">{sentTo}</span> to accept it and log
              in.
            </p>
          </div>
        ) : null}

        {isLoading ? (
          <div className="overflow-hidden rounded-md border border-border">
            {[1, 2].map((i) => (
              <Skeleton
                className="h-10 w-full rounded-none border-b border-border/70 last:border-0"
                key={i}
              />
            ))}
          </div>
        ) : error ? (
          <Alert variant="destructive">Failed to load members: {(error as Error).message}</Alert>
        ) : !members || members.length === 0 ? (
          <p className="text-sm text-muted-foreground">No members in this project yet.</p>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <Table className="w-full [&_td:first-child]:pl-4 [&_th:first-child]:pl-4">
              <TableHeader>
                <TableRow>
                  <TableHead className="h-8 py-1">User</TableHead>
                  {showActions ? (
                    <TableHead className="h-8 w-16 py-1 text-center">Actions</TableHead>
                  ) : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => {
                  const isSelf = myUserId != null && member.user === myUserId;
                  return (
                    <TableRow className="hover:bg-wash-raised" key={member.id}>
                      <TableCell className="py-1.5 font-mono text-sm">
                        {member.userEmail ?? `User #${member.user}`}
                      </TableCell>
                      {showActions ? (
                        <TableCell className="py-1.5 text-center">
                          {isSelf || !canRemoveOthers ? null : (
                            <ConfirmDialog
                              confirmLabel="Remove member"
                              description={`Remove ${member.userEmail ?? "this member"} from the project? They will lose access.`}
                              destructive
                              isPending={removeMember.isPending}
                              keepOpenOnError
                              onConfirm={() => removeMember.mutateAsync(member.id)}
                              title="Remove member?"
                              trigger={
                                <Button
                                  aria-label="Remove member"
                                  className="text-destructive hover:text-destructive"
                                  disabled={removeMember.isPending}
                                  size="icon-xs"
                                  variant="ghost"
                                >
                                  <Icon.delete />
                                </Button>
                              }
                            />
                          )}
                        </TableCell>
                      ) : null}
                    </TableRow>
                  );
                })}
                {invites.map((invite) => (
                  <TableRow className="hover:bg-wash-raised" key={invite.id}>
                    <TableCell className="py-1.5 font-mono text-sm">
                      <span className="flex items-center gap-2">
                        {invite.email}
                        <Badge size="chip" variant="secondary">
                          Invited
                        </Badge>
                      </span>
                    </TableCell>
                    {showActions ? (
                      <TableCell className="py-1.5 text-center">
                        <ConfirmDialog
                          confirmLabel="Revoke invitation"
                          description={`Revoke the invitation for ${invite.email}? Their sign-up link stops granting access to this project.`}
                          destructive
                          isPending={revokeInvite.isPending}
                          keepOpenOnError
                          onConfirm={() => revokeInvite.mutateAsync(invite.id)}
                          title="Revoke invitation?"
                          trigger={
                            <Button
                              aria-label="Revoke invitation"
                              className="text-destructive hover:text-destructive"
                              disabled={revokeInvite.isPending}
                              size="icon-xs"
                              variant="ghost"
                            >
                              <Icon.delete />
                            </Button>
                          }
                        />
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </section>
  );
}
