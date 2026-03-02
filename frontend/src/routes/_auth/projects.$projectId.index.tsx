import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Delete as Trash2, Loader as Loader2, Lock as Key, Plus } from "pixelarticons/react";

import apiClient from "@/client";
import { CreateApiKeyDialog } from "@/components/create-api-key-dialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useProjectQuery } from "@/hooks/use-query";
import { useDeleteToken, useTokensList } from "@/hooks/use-tokens";
import { formatDate } from "@/lib/utils";

export const Route = createFileRoute("/_auth/projects/$projectId/")({
  component: ProjectDetailPage,
});

function ProjectDetailPage() {
  const { projectId } = Route.useParams();
  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-center gap-4">
        <Button asChild size="sm" variant="ghost">
          <Link search={(prev) => prev} to="..">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
      </div>
      <ProjectDetailCard projectId={projectId} />
      <ProjectApiKeys projectId={projectId} />
    </div>
  );
}

function ProjectDetailCard({ projectId }: { projectId: string }) {
  const { data, isLoading, error } = useProjectQuery(projectId);
  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="size-10 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="destructive">Failed to load project: {(error as Error).message}</Alert>;
  }

  if (!data) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <p className="text-muted-foreground">Project not found</p>
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-semibold">Project Details</h2>
        <p className="text-xs text-muted-foreground">Basic information about this project</p>
      </CardHeader>
      <CardContent>
        <Table>
          <TableBody>
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground w-[35%]">
                Name
              </TableCell>
              <TableCell className="font-medium">{data.name}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground">Project ID</TableCell>
              <TableCell className="font-mono text-sm">{data.projectId}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground">Slug</TableCell>
              <TableCell className="font-mono text-sm">{data.slug}</TableCell>
            </TableRow>
            {data.description && (
              <TableRow>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  Description
                </TableCell>
                <TableCell className="text-sm">{data.description}</TableCell>
              </TableRow>
            )}
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground">
                Organisation
              </TableCell>
              <TableCell>{data.organisationName}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground">Members</TableCell>
              <TableCell>{data.memberCount ?? "—"}</TableCell>
            </TableRow>
            <TableRow>
              <TableCell className="font-mono text-xs text-muted-foreground">Created</TableCell>
              <TableCell>{formatDate(data.createdAt?.toISOString())}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// ─── API Keys list ────────────────────────────────────────────────────────────

interface ApiKeysListProps {
  projectId: string;
}

function ApiKeysList({ projectId }: ApiKeysListProps) {
  const { data: tokenData, isLoading, error } = useTokensList(projectId);
  const deleteToken = useDeleteToken();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton className="h-12 w-full" key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return <Alert variant="destructive">Failed to load API keys: {(error as Error).message}</Alert>;
  }

  if (!tokenData || tokenData.tokens.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <Key className="mb-4 size-12 text-muted-foreground" />
        <p className="text-muted-foreground">No API keys for this project yet.</p>
        <p className="mt-1 text-sm text-muted-foreground">Create one using the button above.</p>
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Roles</TableHead>
          <TableHead>Created</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead className="w-[80px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {tokenData.tokens.map((token) => (
          <TableRow key={token.tokenId}>
            <TableCell className="font-medium">{token.name ?? "Unnamed"}</TableCell>
            <TableCell>{token.roles?.join(", ") ?? "—"}</TableCell>
            <TableCell>{formatDate(token.createdAt.toISOString())}</TableCell>
            <TableCell>{formatDate(token.expiresAt?.toISOString())}</TableCell>
            <TableCell>
              <Button
                aria-label="Delete API key"
                className="text-destructive hover:text-destructive"
                disabled={deleteToken.isPending}
                onClick={() => {
                  if (confirm("Delete this API key? It will stop working immediately.")) {
                    deleteToken.mutate(token.tokenId);
                  }
                }}
                size="icon"
                variant="ghost"
              >
                <Trash2 className="size-4" />
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ─── Orchestrator ─────────────────────────────────────────────────────────────

function ProjectApiKeys({ projectId }: { projectId: string }) {
  const { data: project } = useProjectQuery(projectId);
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);

  const handleCreated = () => {
    queryClient.invalidateQueries({ queryKey: ["tokens", projectId] });
  };

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <div>
            <h2 className="text-lg font-semibold">API Keys</h2>
            <p className="text-xs text-muted-foreground">
              API keys for this project. The full key is only shown when created.
            </p>
          </div>
          <Button onClick={() => setShowCreateModal(true)} size="sm">
            <Plus className="mr-2 size-4" />
            Create API Key
          </Button>
        </CardHeader>
        <CardContent>
          <ApiKeysList projectId={projectId} />
        </CardContent>
      </Card>

      <CreateApiKeyDialog
        onCreated={handleCreated}
        onOpenChange={setShowCreateModal}
        open={showCreateModal}
        organisationId={project?.organisationId ?? ""}
        projectId={projectId}
      />
    </>
  );
}
