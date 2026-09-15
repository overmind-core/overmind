import { useState } from "react";

import apiClient from "@/client";
import { McpClientPicker, McpConnectSnippet } from "@/components/mcp-setup-panel";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { featureFlags } from "@/lib/feature-flags";
import type { McpClient } from "@/lib/mcp-setup";
import { notify } from "@/lib/notify";
import { APITokenScopeScopeEnum } from "@/openapi";

const EXPIRY_OPTIONS = [
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 180, label: "180 days" },
  { days: 365, label: "1 year" },
  { days: 0, label: "Never" },
] as const;

interface CreateApiKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  onCreated?: (key: string) => void;
}

export function CreateApiKeyDialog({
  open,
  onOpenChange,
  projectId,
  onCreated,
}: CreateApiKeyDialogProps) {
  const [keyName, setKeyName] = useState(() => `API Key - ${Date.now()}`);
  const [keyDescription, setKeyDescription] = useState("api key for overmind");
  const [expiryDays, setExpiryDays] = useState("365");
  const [projectScoped, setProjectScoped] = useState(true);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [createError, setCreateError] = useState("");
  const [createPending, setCreatePending] = useState(false);
  const [copied, setCopied] = useState(false);
  const [mcpClient, setMcpClient] = useState<McpClient>("cursor");

  const handleCreate = async () => {
    setCreateError("");
    setCreatePending(true);
    try {
      const name = keyName.trim() || `API Key - ${Date.now()}`;
      const tokenResponse = await apiClient.auth.authApiKeysCreate({
        aPITokenCreateRequestRequest: projectScoped
          ? { name, project: projectId }
          : { name, scope: { scope: APITokenScopeScopeEnum.account } },
      });

      setNewToken(tokenResponse.key);
      onCreated?.(tokenResponse.key);
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
    setProjectScoped(true);
  };

  const handleCopyKey = async () => {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken);
    } catch {
      notify.error("Couldn't copy — select the key text and copy it manually.");
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Dialog onOpenChange={(open) => !open && handleClose()} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>
            {newToken
              ? "Copy your key now — you won't be able to see it again."
              : "Fill in the details below to generate a new API key."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {newToken ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <p className="text-sm font-medium">Your new API key:</p>
                <div className="overflow-x-auto rounded-md border border-dashed border-warning/50 bg-warning/10 p-3 font-mono text-sm">
                  <code className="select-text break-all">{newToken}</code>
                </div>
                <Button onClick={handleCopyKey} size="sm" variant={copied ? "default" : "outline"}>
                  {copied ? (
                    <>
                      <Icon.success />
                      Copied!
                    </>
                  ) : (
                    "Copy to clipboard"
                  )}
                </Button>
              </div>
              {featureFlags.mcp && (
                <div className="space-y-2">
                  <p className="text-sm font-medium">Add Overmind MCP to your coding agent</p>
                  <McpClientPicker onChange={setMcpClient} value={mcpClient} />
                  <McpConnectSnippet apiKey={newToken} client={mcpClient} projectId={projectId} />
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  onChange={(e) => setKeyName(e.target.value)}
                  placeholder="My API key"
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
              <div className="flex items-start justify-between gap-3 rounded-md border border-border px-3 py-3">
                <div className="space-y-0.5">
                  <Label htmlFor="key-scope">Project-specific</Label>
                  <p className="text-xs text-muted-foreground">
                    {projectScoped
                      ? "Pinned to this project."
                      : "Account key — every project you belong to."}
                  </p>
                </div>
                <Switch checked={projectScoped} id="key-scope" onCheckedChange={setProjectScoped} />
              </div>
              {createError && <Alert variant="destructive">{createError}</Alert>}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          {newToken ? (
            <Button onClick={handleClose}>
              <Icon.success />
              Done
            </Button>
          ) : (
            <>
              <Button onClick={handleClose} variant="secondary">
                <Icon.close />
                Cancel
              </Button>
              <Button disabled={createPending} onClick={handleCreate}>
                {createPending ? (
                  <>
                    <Spinner className="text-current" size="sm" />
                    Creating…
                  </>
                ) : (
                  "Create API key"
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
