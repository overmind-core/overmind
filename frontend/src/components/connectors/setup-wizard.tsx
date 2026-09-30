import { useEffect, useState } from "react";
import type { DateRange } from "react-day-picker";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, format, startOfDay, subDays, subMonths } from "date-fns";

import api from "@/client";
import type { ConnectorMeta } from "@/components/connectors";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Dialog,
  DialogBody,
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
import { Spinner } from "@/components/ui/spinner";
import { errorMessage } from "@/lib/notify";
import type { ConnectorCredential, ConnectorTypeEnum, ImportPreview } from "@/openapi";

export interface ConnectorSetupWizardProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  meta: ConnectorMeta;
  existing?: ConnectorCredential;
  onCompleted?: (info: { credentialId: string; targetProjectId: string }) => void;
}

const ranges = [
  ["all", "All available history"],
  ["6months", "Last 6 months"],
  ["3months", "Last 3 months"],
  ["30days", "Last 30 days"],
  ["7days", "Last 7 days"],
  ["1day", "Last 24 hours"],
  ["custom", "Custom range"],
];

function duration(seconds: number) {
  return seconds < 60
    ? `${seconds} sec`
    : seconds < 3600
      ? `${Math.ceil(seconds / 60)} min`
      : `${Math.ceil(seconds / 3600)} hr`;
}

export function ConnectorSetupWizard(props: ConnectorSetupWizardProps) {
  if (!props.open) return null;
  return (
    <ImportDialog key={`${props.existing?.id ?? props.meta.type}:${props.projectId}`} {...props} />
  );
}

function ImportDialog({
  open,
  onClose,
  projectId,
  meta,
  existing,
  onCompleted,
}: ConnectorSetupWizardProps) {
  const qc = useQueryClient();
  const [credentialId, setCredentialId] = useState(existing?.id ?? "");
  const [name, setName] = useState(existing?.name ?? meta.label);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [baseUrl, setBaseUrl] = useState(existing?.baseUrl ?? "");
  const [range, setRange] = useState("30days");
  const [dates, setDates] = useState<DateRange>();
  const [credentialsDirty, setCredentialsDirty] = useState(!existing);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const [source, setSource] = useState(existing?.activeConfig?.sourceProjectId ?? "");
  const sourceLabel = meta.type === "galileo" ? "Source log stream" : "Source project";
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const verification = useQuery({
    enabled: !!credentialId && !credentialsDirty,
    queryFn: () => api.connectorCredentials.connectorCredentialsVerifyCreate({ id: credentialId }),
    queryKey: ["connector-verify", credentialId, connectionRevision],
    retry: false,
  });
  const projects = verification.data?.projects ?? [];
  useEffect(() => {
    if (!credentialsDirty && verification.data?.ok && !projects.some((p) => p.id === source)) {
      setSource(projects.length === 1 ? projects[0].id : "");
      setPreview(null);
    }
  }, [projects, source, verification.data?.ok, credentialsDirty]);
  const sourceProject = projects.find((p) => p.id === source);
  const previewQuery = useQuery({
    enabled: !!preview?.id,
    queryFn: () =>
      api.connectorCredentials.connectorCredentialsPreviewRetrieve({
        id: credentialId,
        previewId: preview?.id ?? "",
      }),
    queryKey: ["connector-preview", credentialId, preview?.id],
    refetchInterval: (q) =>
      ["queued", "running"].includes(q.state.data?.status ?? "queued") ? 2000 : false,
  });
  const current = previewQuery.data ?? preview;
  const busyCounting = !!current && ["queued", "running"].includes(current.status);
  const ready =
    current?.status === "ready" && !!current.expiresAt && current.expiresAt > new Date();
  const connect = useMutation({
    mutationFn: () =>
      credentialId
        ? api.connectorCredentials.connectorCredentialsPartialUpdate({
            id: credentialId,
            patchedConnectorCredentialRequest: {
              baseUrl,
              name,
              ...(apiKey ? { apiKey } : {}),
              ...(apiSecret ? { apiSecret } : {}),
            },
          })
        : api.connectorCredentials.connectorCredentialsCreate({
            connectorCredentialRequest: {
              apiKey,
              apiSecret,
              baseUrl,
              connectorType: meta.type as ConnectorTypeEnum,
              name,
              project: projectId,
            },
          }),
    onError: async (e) => setError(await errorMessage(e, "Could not connect.")),
    onSuccess: (created) => {
      setCredentialId(created.id);
      setCredentialsDirty(false);
      setConnectionRevision((version) => version + 1);
      setApiKey("");
      setApiSecret("");
      setError(null);
    },
  });
  const count = useMutation({
    mutationFn: () => {
      const end =
        range === "custom" && dates?.to
          ? new Date(Math.min(addDays(startOfDay(dates.to), 1).getTime(), Date.now()))
          : new Date();
      const start =
        range === "all"
          ? null
          : range === "custom"
            ? startOfDay(dates!.from!)
            : range === "6months"
              ? subMonths(end, 6)
              : range === "3months"
                ? subMonths(end, 3)
                : subDays(end, range === "30days" ? 30 : range === "7days" ? 7 : 1);
      return api.connectorCredentials.connectorCredentialsPreviewCreate({
        id: credentialId,
        importRangeRequest: { backfillFrom: start, backfillTo: end, sourceProjectId: source },
      });
    },
    onError: async (e) => setError(await errorMessage(e, "Could not count traces.")),
    onSuccess: (result) => {
      setPreview(result);
      setError(null);
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      api.connectorCredentials.connectorCredentialsImportCreate({
        confirmImportRequest: { previewId: current?.id ?? "" },
        id: credentialId,
      }),
    onError: async (e) => setError(await errorMessage(e, "Could not start the import.")),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
      onCompleted?.({ credentialId, targetProjectId: projectId });
      onClose();
    },
  });
  const resetPreview = () => {
    setPreview(null);
    setError(null);
  };
  const changeCredentials = () => {
    setCredentialsDirty(true);
    resetPreview();
  };
  const connected = !credentialsDirty && !!verification.data?.ok && !verification.isFetching;
  const customValid = range !== "custom" || (!!dates?.from && !!dates.to && dates.from <= dates.to);
  const message =
    error ??
    (verification.isError
      ? "Could not verify this connection. Check its credentials and retry."
      : current?.error || (previewQuery.isError ? "Could not check the count. Try again." : null));
  return (
    <Dialog
      onOpenChange={(value) => {
        if (!value && !confirm.isPending) onClose();
      }}
      open={open}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Import from {meta.label}</DialogTitle>
          <DialogDescription>
            Connect a source, choose a range and confirm the trace count.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-5">
          {message && <Alert variant="destructive">{message}</Alert>}
          <form
            className="space-y-4"
            id="connector-connect"
            onSubmit={(e) => {
              e.preventDefault();
              if (credentialsDirty || !credentialId) connect.mutate();
              else verification.refetch();
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="connector-name">Connection name</Label>
              <Input
                id="connector-name"
                onChange={(e) => {
                  setName(e.target.value);
                  changeCredentials();
                }}
                required
                value={name}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="connector-key">{meta.apiKeyLabel}</Label>
              <Input
                autoComplete="off"
                id="connector-key"
                onChange={(e) => {
                  setApiKey(e.target.value);
                  changeCredentials();
                }}
                placeholder={credentialId ? "Saved key" : meta.apiKeyPlaceholder}
                required={!credentialId}
                type="password"
                value={apiKey}
              />
            </div>
            {meta.needsSecret && (
              <div className="space-y-2">
                <Label htmlFor="connector-secret">{meta.secretLabel}</Label>
                <Input
                  autoComplete="off"
                  id="connector-secret"
                  onChange={(e) => {
                    setApiSecret(e.target.value);
                    changeCredentials();
                  }}
                  placeholder={credentialId ? "Saved secret" : meta.secretPlaceholder}
                  required={!credentialId}
                  type="password"
                  value={apiSecret}
                />
              </div>
            )}
            {meta.needsBaseUrl && (
              <div className="space-y-2">
                <Label htmlFor="connector-host">{meta.baseUrlLabel ?? "Host URL"}</Label>
                <Input
                  id="connector-host"
                  onChange={(e) => {
                    setBaseUrl(e.target.value);
                    changeCredentials();
                  }}
                  placeholder={meta.baseUrlPlaceholder}
                  type="url"
                  value={baseUrl}
                />
                <p className="text-xs text-muted-foreground">{meta.baseUrlHint || "Optional"}</p>
              </div>
            )}
            <a
              className="text-sm underline underline-offset-4"
              href={meta.docsUrl}
              rel="noreferrer"
              target="_blank"
            >
              Find your {meta.label} keys
            </a>
            <div className="flex items-center gap-3">
              <Button
                disabled={
                  connect.isPending ||
                  verification.isFetching ||
                  !name ||
                  (!credentialId && (!apiKey || (meta.needsSecret && !apiSecret)))
                }
                type="submit"
                variant="secondary"
              >
                {(connect.isPending || verification.isFetching) && <Spinner size="sm" />}
                {credentialsDirty || !credentialId ? "Load sources" : "Refresh sources"}
              </Button>
              {connected && <span className="text-xs text-success">Connected</span>}
            </div>
          </form>
          <div className="space-y-2 border-t border-border/70 pt-4">
            <Label htmlFor="connector-source">{sourceLabel}</Label>
            <Select
              disabled={!connected || !projects.length || count.isPending || confirm.isPending}
              onValueChange={(value) => {
                setSource(value);
                resetPreview();
              }}
              value={source}
            >
              <SelectTrigger id="connector-source">
                <SelectValue
                  placeholder={
                    credentialId ? `Select a ${sourceLabel.toLowerCase()}` : "Load sources first"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {projects.map((project) => (
                  <SelectItem key={project.id} value={project.id}>
                    {project.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {connected && sourceProject && (
              <p className="break-all text-xs text-muted-foreground">ID: {sourceProject.id}</p>
            )}
            {connected && !projects.length && (
              <Alert>No {sourceLabel.toLowerCase()}s are available for these credentials.</Alert>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="connector-range">Time range</Label>
            <Select
              disabled={count.isPending || confirm.isPending}
              onValueChange={(v) => {
                setRange(v);
                resetPreview();
              }}
              value={range}
            >
              <SelectTrigger id="connector-range">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ranges.map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {range === "custom" && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-4 text-sm">
                <p>From: {dates?.from ? format(dates.from, "PPP") : "Select a date"}</p>
                <p>To: {dates?.to ? format(dates.to, "PPP") : "Select a date"}</p>
              </div>
              <Calendar
                captionLayout="dropdown"
                className="mx-auto"
                disabled={{ after: new Date() }}
                endMonth={new Date()}
                mode="range"
                onSelect={(value) => {
                  setDates(value);
                  resetPreview();
                }}
                selected={dates}
              />
              <p className="text-xs text-muted-foreground">
                Select a start and end date. Dates use your local time.
              </p>
            </div>
          )}
          {(busyCounting || count.isPending) && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Spinner size="sm" />
              {current?.status === "queued"
                ? "Waiting for a count worker…"
                : `Counting traces… ${(current?.traceCount ?? 0).toLocaleString()} found`}
            </p>
          )}
          {current?.status === "ready" && (
            <div aria-live="polite" className="space-y-3 border-t border-border pt-4">
              <dl className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <dt className="text-muted-foreground">Traces available</dt>
                  <dd className="font-medium tabular-nums">
                    {current.traceCount.toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Estimated import time</dt>
                  <dd className="font-medium">
                    {duration(current.estimatedSecondsMin)}–{duration(current.estimatedSecondsMax)}
                  </dd>
                </div>
              </dl>
              <p className="text-xs text-muted-foreground">
                {current.windowFrom ? current.windowFrom.toLocaleString() : "All available history"}{" "}
                to {current.windowTo.toLocaleString()}
              </p>
              <p className="text-xs text-muted-foreground">
                Provider updates can change the final count and duration. Existing traces are kept.
                New patterns require review.
              </p>
              {!current.traceCount && <p className="text-sm">No traces found in this range.</p>}
              {!ready && (
                <p className="text-sm text-warning">This count expired. Count the range again.</p>
              )}
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <Button disabled={confirm.isPending} onClick={onClose} variant="secondary">
            Cancel
          </Button>
          {ready && current && current.traceCount > 0 ? (
            <Button
              disabled={confirm.isPending || !connected}
              key="confirm-import"
              onClick={() => confirm.mutate()}
            >
              {confirm.isPending && <Spinner size="sm" />}Import{" "}
              {current.traceCount.toLocaleString()} traces
            </Button>
          ) : (
            <Button
              disabled={
                !connected || count.isPending || busyCounting || !sourceProject || !customValid
              }
              key="count-traces"
              onClick={() => {
                resetPreview();
                count.mutate();
              }}
            >
              Count traces
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
