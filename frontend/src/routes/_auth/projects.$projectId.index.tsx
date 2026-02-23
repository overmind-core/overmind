import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, Check, Key, Loader2, Plus, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

const EXPIRY_OPTIONS = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "180 days", days: 180 },
  { label: "1 year", days: 365 },
  { label: "Never", days: 0 },
] as const;

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
          <TableHead>Prefix</TableHead>
          <TableHead>Created</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead className="w-[80px]" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {tokenData.tokens.map((token) => (
          <TableRow key={token.tokenId}>
            <TableCell className="font-medium">{token.name ?? "Unnamed"}</TableCell>
            <TableCell className="font-mono text-sm text-muted-foreground">
              {token.prefix ?? "—"}
            </TableCell>
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

// ─── Create API Key dialog ────────────────────────────────────────────────────

interface CreateApiKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onCreated: () => void;
}

function CreateApiKeyDialog({
  open,
  onOpenChange,
  projectId,
  onCreated,
}: CreateApiKeyDialogProps) {
  const [keyName, setKeyName] = useState(() => `API Key - ${Date.now()}`);
  const [keyDescription, setKeyDescription] = useState("api key for overmind");
  const [expiryDays, setExpiryDays] = useState("365");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [createError, setCreateError] = useState("");
  const [createPending, setCreatePending] = useState(false);
  const [copied, setCopied] = useState(false);

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

  const handleCreate = async () => {
    setCreateError("");
    setCreatePending(true);
    try {
      const authToken = localStorage.getItem("token");
      const days = parseInt(expiryDays, 10);
      const res = await fetch(`${baseUrl}/api/v1/iam/tokens/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        },
        body: JSON.stringify({
          name: keyName.trim() || `API Key - ${Date.now()}`,
          description: keyDescription.trim() || undefined,
          project_id: projectId,
          expires_in_days: days > 0 ? days : undefined,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail?.message ?? data?.detail ?? "Failed to create API key");
      }

      const tokenResponse = await res.json();
      setNewToken(tokenResponse.token);
      onCreated();
    } catch (err) {
      setCreateError((err as Error).message ?? "Failed to create API key");
    } finally {
      setCreatePending(false);
    }
  };

  const handleClose = () => {
    onOpenChange(false);
    setNewToken(null);
    setCreateError("");
    setCopied(false);
    setKeyName(() => `API Key - ${Date.now()}`);
    setExpiryDays("365");
  };

  const handleCopyKey = async () => {
    if (!newToken) return;
    await navigator.clipboard.writeText(newToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog onOpenChange={(isOpen) => !isOpen && handleClose()} open={open}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Create API Key</DialogTitle>
          <DialogDescription>
            {newToken
              ? "Copy your key now — you won't be able to see it again."
              : "Fill in the details below to generate a new API key for this project."}
          </DialogDescription>
        </DialogHeader>

        {newToken ? (
          <div className="space-y-2">
            <p className="text-sm font-medium">Your new API key:</p>
            <div className="overflow-x-auto rounded-md border border-dashed border-amber-500/50 bg-amber-50/50 p-3 font-mono text-sm dark:bg-amber-950/20">
              <code className="select-text break-all">{newToken}</code>
            </div>
            <Button onClick={handleCopyKey} size="sm" variant={copied ? "default" : "outline"}>
              {copied ? (
                <>
                  <Check className="mr-2 size-4" />
                  Copied!
                </>
              ) : (
                "Copy to clipboard"
              )}
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                onChange={(e) => setKeyName(e.target.value)}
                placeholder="My API Key"
                value={keyName}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="key-description">Description</Label>
              <Input
                id="key-description"
                onChange={(e) => setKeyDescription(e.target.value)}
                placeholder="api key for overmind"
                value={keyDescription}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="key-expiry">Expires after</Label>
              <Select onValueChange={setExpiryDays} value={expiryDays}>
                <SelectTrigger id="key-expiry">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.days} value={String(opt.days)}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {createError && <Alert variant="destructive">{createError}</Alert>}
          </div>
        )}

        <DialogFooter>
          {newToken ? (
            <Button onClick={handleClose}>Done</Button>
          ) : (
            <>
              <Button onClick={handleClose} variant="outline">
                Cancel
              </Button>
              <Button disabled={createPending} onClick={handleCreate}>
                {createPending && <Loader2 className="mr-2 size-4 animate-spin" />}
                Create
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Orchestrator ─────────────────────────────────────────────────────────────

function ProjectApiKeys({ projectId }: { projectId: string }) {
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
        projectId={projectId}
      />
    </>
  );
}
