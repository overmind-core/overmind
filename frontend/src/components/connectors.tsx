import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import galileoLogo from "@/assets/galileo.png";
import api from "@/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
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
import { errorMessage } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import type { ConnectorCredential } from "@/openapi";

//
// Consumed by the Integrations page (under Observability); kept as a registry
// so every surface renders identical connector affordances without duplicating
// provider metadata or the connect dialog.

export interface ConnectorMeta {
  type: string;
  label: string;
  description: string;
  docsUrl: string;
  logoUrl?: string;
  needsBaseUrl: boolean;
  baseUrlLabel?: string;
  /** Qualifier shown after the label, where the field can be left blank. */
  baseUrlHint?: string;
  baseUrlPlaceholder: string;
  apiKeyLabel: string;
  apiKeyPlaceholder: string;
  needsSecret: boolean;
  secretLabel: string;
  secretPlaceholder: string;
  /** Whether this connector is fully implemented. Others show "coming soon". */
  available: boolean;
}

export const CONNECTORS: ConnectorMeta[] = [
  {
    apiKeyLabel: "Public key",
    apiKeyPlaceholder: "pk-lf-...",
    available: true,
    baseUrlHint: "(optional for cloud)",
    baseUrlLabel: "Host URL",
    baseUrlPlaceholder: "https://cloud.langfuse.com",
    description: "Open-source LLM observability. Import traces, evaluations, and prompt versions.",
    docsUrl: "https://langfuse.com/faq/all/where-are-langfuse-api-keys",
    label: "Langfuse",
    logoUrl: "https://langfuse.com/apple-touch-icon.png",
    needsBaseUrl: true,
    needsSecret: true,
    secretLabel: "Secret key",
    secretPlaceholder: "sk-lf-...",
    type: "langfuse",
  },
  {
    apiKeyLabel: "API key",
    apiKeyPlaceholder: "lsv2_sk_... or lsv2_pt_...",
    available: true,
    baseUrlHint: "(optional — EU is eu.api.smith.langchain.com)",
    baseUrlLabel: "API URL",
    baseUrlPlaceholder: "https://api.smith.langchain.com",
    description: "LangChain observability. Import traces, runs, and threads.",
    docsUrl: "https://docs.langchain.com/langsmith/authentication-methods",
    label: "LangSmith",
    logoUrl: "https://smith.langchain.com/favicon.ico",
    needsBaseUrl: true,
    needsSecret: false,
    secretLabel: "",
    secretPlaceholder: "",
    type: "langsmith",
  },
  {
    apiKeyLabel: "API key or service token",
    apiKeyPlaceholder: "bt-st-...",
    available: true,
    baseUrlLabel: "API URL",
    baseUrlPlaceholder: "https://api.braintrust.dev",
    description: "Eval and observability platform. Import project logs, spans, and scores.",
    docsUrl: "https://www.braintrust.dev/docs/admin/authentication",
    label: "Braintrust",
    logoUrl: "https://www.braintrust.dev/apple-touch-icon.png",
    needsBaseUrl: true,
    needsSecret: false,
    secretLabel: "",
    secretPlaceholder: "",
    type: "braintrust",
  },
  {
    apiKeyLabel: "API key",
    apiKeyPlaceholder: "...",
    available: true,
    baseUrlHint: "(optional — self-hosted only)",
    baseUrlLabel: "API URL",
    baseUrlPlaceholder: "https://api.galileo.ai",
    description: "LLM evaluation and observability. Import traces, spans, and metrics.",
    docsUrl: "https://docs.galileo.ai/references/faqs/find-keys",
    label: "Galileo",
    logoUrl: galileoLogo,
    needsBaseUrl: true,
    needsSecret: false,
    secretLabel: "",
    secretPlaceholder: "",
    type: "galileo",
  },
];

export interface ConnectorDialogProps {
  open: boolean;
  onClose: () => void;
  meta: ConnectorMeta;
  projectId: string;
  existing?: ConnectorCredential;
  /** Optional callback fired after a successful create/update. */
  onSaved?: () => void;
}

export function ConnectorDialog({
  open,
  onClose,
  meta,
  projectId,
  existing,
  onSaved,
}: ConnectorDialogProps) {
  const qc = useQueryClient();
  const [name, setName] = useState(existing?.name ?? meta.label);
  const [baseUrl, setBaseUrl] = useState(existing?.baseUrl ?? "");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [error, setError] = useState<string | null>(null);

  const isEdit = !!existing;

  const saveMutation = useMutation({
    mutationFn: () => {
      if (isEdit) {
        return api.connectorCredentials.connectorCredentialsPartialUpdate({
          id: existing!.id,
          patchedConnectorCredentialRequest: {
            baseUrl,
            name,
            ...(apiKey ? { apiKey } : {}),
            ...(apiSecret ? { apiSecret } : {}),
          },
        });
      }
      return api.connectorCredentials.connectorCredentialsCreate({
        connectorCredentialRequest: {
          apiKey,
          apiSecret,
          baseUrl,
          connectorType: meta.type as ConnectorCredential["connectorType"],
          name,
          project: projectId,
        },
      });
    },
    onError: (e: unknown) => setError(errorMessage(e, "Couldn't save the connection")),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
      onSaved?.();
      onClose();
    },
  });

  return (
    <Dialog onOpenChange={(v) => !v && onClose()} open={open}>
      <DialogContent size="sm">
        <DialogHeader>
          <div className="flex items-center gap-3">
            {meta.logoUrl && (
              <img alt={meta.label} className="size-8 shrink-0 rounded-md" src={meta.logoUrl} />
            )}
            <div>
              <DialogTitle>{isEdit ? `Edit ${meta.label}` : `Connect ${meta.label}`}</DialogTitle>
              <DialogDescription className="mt-0.5 text-xs">{meta.description}</DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4 pt-1">
            <div>
              <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="conn-name">
                Connection name
              </Label>
              <Input
                id="conn-name"
                onChange={(e) => setName(e.target.value)}
                placeholder={meta.label}
                value={name}
              />
            </div>

            {meta.needsBaseUrl && (
              <div>
                <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="conn-url">
                  {meta.baseUrlLabel ?? "Base URL"}
                  {meta.baseUrlHint ? (
                    <span className="ml-1 text-muted-foreground/50">{meta.baseUrlHint}</span>
                  ) : null}
                </Label>
                <Input
                  id="conn-url"
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={meta.baseUrlPlaceholder}
                  value={baseUrl}
                />
              </div>
            )}

            <div>
              <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="conn-key">
                {meta.apiKeyLabel}
                {isEdit && (
                  <span className="ml-1 text-muted-foreground/50">
                    (leave blank to keep current)
                  </span>
                )}
              </Label>
              <Input
                autoComplete="off"
                className="font-mono"
                id="conn-key"
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={isEdit ? existing?.apiKeyHint || "••••••••" : meta.apiKeyPlaceholder}
                type="password"
                value={apiKey}
              />
            </div>

            {meta.needsSecret && (
              <div>
                <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="conn-secret">
                  {meta.secretLabel}
                  {isEdit && (
                    <span className="ml-1 text-muted-foreground/50">
                      (leave blank to keep current)
                    </span>
                  )}
                </Label>
                <Input
                  autoComplete="off"
                  className="font-mono"
                  id="conn-secret"
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder={isEdit ? "••••••••" : meta.secretPlaceholder}
                  type="password"
                  value={apiSecret}
                />
              </div>
            )}

            <a
              className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              href={meta.docsUrl}
              rel="noreferrer"
              target="_blank"
            >
              <Icon.externalLink className="size-4" />
              Where to find your credentials
            </a>

            <p className={`${PROSE} text-xs text-muted-foreground/70`}>
              Imported traces are not live-scored. Native SDK telemetry is.
            </p>

            {error && (
              <div className="flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive/5 p-3">
                <Icon.warning className="mt-0.5 size-4 shrink-0 text-destructive" />
                <p className="text-sm text-destructive">{error}</p>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-1">
              <Button onClick={onClose} variant="secondary">
                <Icon.close />
                Cancel
              </Button>
              <Button
                disabled={saveMutation.isPending || !name || (!isEdit && !apiKey)}
                onClick={() => saveMutation.mutate()}
              >
                {saveMutation.isPending ? (
                  <>
                    <Spinner size="sm" />
                    Saving…
                  </>
                ) : isEdit ? (
                  "Save changes"
                ) : (
                  "Connect"
                )}
              </Button>
            </div>
          </div>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

const POLL_INTERVAL_PRESETS = [
  { label: "Every 1 minute", seconds: 60 },
  { label: "Every 5 minutes", seconds: 300 },
  { label: "Every 15 minutes", seconds: 900 },
  { label: "Every 30 minutes", seconds: 1800 },
] as const;

const MIN_POLL_INTERVAL_SECONDS = 60;
const MAX_POLL_INTERVAL_SECONDS = 24 * 60 * 60;

function clampPollMinutes(minutes: number): number {
  const seconds = Math.round(minutes * 60);
  return Math.min(MAX_POLL_INTERVAL_SECONDS, Math.max(MIN_POLL_INTERVAL_SECONDS, seconds));
}

export function PollIntervalSelect({
  seconds,
  onChange,
  disabled,
}: {
  seconds: number;
  onChange: (seconds: number) => void;
  disabled?: boolean;
}) {
  // Custom is a mode, not a derived state: a custom value can coincide with a
  // preset (5 min), and deriving would snap the picker back to that preset.
  const [custom, setCustom] = useState(
    () => !POLL_INTERVAL_PRESETS.some((p) => p.seconds === seconds)
  );
  const [customMinutes, setCustomMinutes] = useState(() => Math.round(seconds / 60) || 1);

  return (
    <div className="flex items-center gap-1.5">
      <Select
        disabled={disabled}
        onValueChange={(v) => {
          setCustom(v === "custom");
          onChange(v === "custom" ? clampPollMinutes(customMinutes) : Number(v));
        }}
        value={custom ? "custom" : String(seconds)}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {POLL_INTERVAL_PRESETS.map((p) => (
            <SelectItem key={p.seconds} value={String(p.seconds)}>
              {p.label}
            </SelectItem>
          ))}
          <SelectItem value="custom">Custom</SelectItem>
        </SelectContent>
      </Select>
      {custom && (
        <div className="flex items-center gap-1">
          <Input
            className="w-16"
            disabled={disabled}
            max={Math.floor(MAX_POLL_INTERVAL_SECONDS / 60)}
            min={1}
            onChange={(e) => {
              const minutes = Math.max(1, Number(e.target.value) || 1);
              setCustomMinutes(minutes);
              onChange(clampPollMinutes(minutes));
            }}
            type="number"
            value={customMinutes}
          />
          <span className="text-xs text-muted-foreground">min</span>
        </div>
      )}
    </div>
  );
}
