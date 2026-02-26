import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { Check, Loader2 } from "lucide-react";

import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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

const EXPIRY_OPTIONS = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "180 days", days: 180 },
  { label: "1 year", days: 365 },
  { label: "Never", days: 0 },
] as const;

interface CreateApiKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  organisationId: string;
  onCreated: () => void;
  defaultRole?: string;
}

export function CreateApiKeyDialog({
  open,
  onOpenChange,
  projectId,
  organisationId,
  onCreated,
  defaultRole = "project_admin",
}: CreateApiKeyDialogProps) {
  const [keyName, setKeyName] = useState(() => `API Key - ${Date.now()}`);
  const [keyDescription, setKeyDescription] = useState("api key for overmind");
  const [selectedRoleId, setSelectedRoleId] = useState("");
  const [expiryDays, setExpiryDays] = useState("365");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [createError, setCreateError] = useState("");
  const [createPending, setCreatePending] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data: rolesData, isLoading: rolesLoading } = useQuery({
    enabled: open,
    queryFn: () => apiClient.roles.listCoreRolesApiV1IamRolesGet({ scope: "project" }),
    queryKey: ["roles", "project"],
  });

  useEffect(() => {
    if (!rolesData) return;
    const target = rolesData.roles?.find((r) => r.name === defaultRole);
    setSelectedRoleId(target?.roleId ?? rolesData.roles?.[0]?.roleId ?? "");
  }, [rolesData, defaultRole]);

  const handleCreate = async () => {
    setCreateError("");
    setCreatePending(true);
    try {
      const tokenResponse = await apiClient.tokens.createTokenApiV1IamTokensPost({
        createTokenRequest: {
          description: keyDescription.trim() || undefined,
          name: keyName.trim() || `API Key - ${Date.now()}`,
          organisationId,
          projectId,
        },
      });

      if (selectedRoleId) {
        const days = parseInt(expiryDays, 10);
        await apiClient.tokenRoles.assignTokenRoleApiV1IamTokensTokenIdRolesPost({
          assignTokenRoleRequest: {
            expiresAt: days > 0 ? new Date(Date.now() + 1000 * 60 * 60 * 24 * days) : undefined,
            roleId: selectedRoleId,
            scopeId: projectId,
            scopeType: "project",
          },
          tokenId: tokenResponse.tokenId,
        });
      }

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
    <Dialog onOpenChange={(open) => !open && handleClose()} open={open}>
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
              <Label htmlFor="key-role">Role</Label>
              {rolesLoading ? (
                <Skeleton className="h-9 w-full" />
              ) : (
                <Select onValueChange={setSelectedRoleId} value={selectedRoleId}>
                  <SelectTrigger id="key-role">
                    <SelectValue placeholder="Select a role" />
                  </SelectTrigger>
                  <SelectContent>
                    {rolesData?.roles?.map((role) => (
                      <SelectItem key={role.roleId} value={role.roleId}>
                        {role.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
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
              <Button disabled={createPending || rolesLoading} onClick={handleCreate}>
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
