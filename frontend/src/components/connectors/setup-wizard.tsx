/**
 * One-page connector setup — credentials → project → range → capabilities →
 * destination, stacked like the optimiser wizard. Scroll vertically; no steps.
 */

import { type ReactNode, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import { type ConnectorMeta, PollIntervalSelect } from "@/components/connectors";
import {
  resolveTimeRange,
  TimeRangeButton,
  type TimeRangePreset,
} from "@/components/traces/traces-table-toolbar";
import { Badge } from "@/components/ui/badge";
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
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  SelectableCard,
  SelectableCardGroup,
  selectableCardVariants,
} from "@/components/ui/selectable-card";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { errorMessage } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type {
  ConnectorCapabilities,
  ConnectorCapabilityCandidate,
  ConnectorCapabilityMappingWriteRequestSourceEnum,
  ConnectorCapabilityProposal,
  ConnectorCredential,
  ConnectorShapeCandidate,
} from "@/openapi";

export type SetupSection = "connect" | "source" | "filters" | "capabilities" | "destination";

type ConfirmedSections = Record<SetupSection, boolean>;

type WizardState = {
  credentialId: string | null;
  name: string;
  baseUrl: string;
  apiKey: string;
  apiSecret: string;
  verified: boolean;
  apiVersion: string | null;
  capabilities: ConnectorCapabilities | null;
  projects: { id: string; name: string }[];
  sourceProjectId: string;
  timeRange: TimeRangePreset;
  customTimeRange: { gte?: string; lte?: string };
  capabilitySource: string;
  capabilityKey: string;
  /** Boundary names, for the observation_name source. */
  capabilityNames: string[];
  assignments: Record<string, string>;
  targetProjectId: string;
  autoSyncEnabled: boolean;
  pollIntervalSeconds: number;
  /** Explicit per-section confirm — defaults alone never mark a section done. */
  confirmed: ConfirmedSections;
  error: string | null;
};

type WizardAction =
  | { type: "PATCH"; patch: Partial<WizardState> }
  | { type: "CONFIRM"; section: SetupSection }
  | { type: "RESET"; projectId: string; existing?: ConnectorCredential; defaultName?: string };

/** A metadata key from discovery; `coverage` of 1 means every observation carries it. */
type MetadataKey = { name: string; observations: number; coverage: number };

/**
 * Trace-wide signals label a whole trace and cannot split one at capability level, so
 * they stay out of the primary list — offered only where they're the only signal,
 * and always shown once a saved mapping uses one.
 */
const CAPABILITY_SOURCES = [
  { boundary: true, label: "Observation Names", value: "observation_name" },
  { boundary: true, label: "Metadata Key", value: "metadata" },
  { boundary: false, label: "Trace Name", value: "trace_name" },
  { boundary: false, label: "Tags", value: "tag" },
] as const;

const BOUNDARY_HINT =
  "Pick the observations that start a capability. Each becomes the root of its own Overmind trace, keeping everything beneath it as recorded — so anything nested under a pick belongs to that capability. Widen the import range above if a name you expect is missing.";

/** Days from range start → now for the connector lookback field (null = all history). */
function lookbackDaysFromRange(
  preset: TimeRangePreset,
  custom: { gte?: string; lte?: string }
): number | null {
  const { gte } = resolveTimeRange(preset, custom);
  if (!gte) return null;
  const startMs = new Date(gte).getTime();
  if (Number.isNaN(startMs)) return null;
  return Math.max(1, Math.ceil((Date.now() - startMs) / (24 * 60 * 60 * 1000)));
}

function rangeSummary(preset: TimeRangePreset, custom: { gte?: string; lte?: string }): string {
  if (preset === "all") return "All time";
  if (preset === "custom") {
    const from = custom.gte ? new Date(custom.gte).toLocaleString() : "…";
    const to = custom.lte ? new Date(custom.lte).toLocaleString() : "now";
    return `${from} → ${to}`;
  }
  const labels: Record<string, string> = {
    past1h: "Last 1 hour",
    past7d: "Last 7 days",
    past15m: "Last 15 min",
    past24h: "Last 24 hours",
    past30d: "Last 30 days",
  };
  return labels[preset] ?? preset;
}

function lookbackFromExistingDays(days: number | null | undefined): {
  timeRange: TimeRangePreset;
  customTimeRange: { gte?: string; lte?: string };
} {
  if (days == null) return { customTimeRange: {}, timeRange: "all" };
  if (days <= 1) return { customTimeRange: {}, timeRange: "past24h" };
  if (days <= 7) return { customTimeRange: {}, timeRange: "past7d" };
  if (days <= 30) return { customTimeRange: {}, timeRange: "past30d" };
  const gte = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
  return { customTimeRange: { gte }, timeRange: "custom" };
}

function initialConfirmed(existing?: ConnectorCredential): ConfirmedSections {
  // Re-open of an already-configured connection: sections stay done.
  const configured = Boolean(existing?.activeConfig);
  const verified = Boolean(existing?.verifiedAt);
  return {
    capabilities: configured,
    connect: verified,
    destination: configured,
    filters: configured,
    source: configured,
  };
}

function initialState(
  projectId: string,
  existing?: ConnectorCredential,
  defaultName = ""
): WizardState {
  const cfg = existing?.activeConfig;
  const range = lookbackFromExistingDays(cfg?.lookbackDays ?? 30);
  return {
    apiKey: "",
    apiSecret: "",
    apiVersion: existing?.apiVersion ?? null,
    assignments:
      existing?.capabilityMapping?.assignments &&
      typeof existing.capabilityMapping.assignments === "object"
        ? (existing.capabilityMapping.assignments as Record<string, string>)
        : {},
    autoSyncEnabled: existing?.autoSyncEnabled ?? true,
    baseUrl: existing?.baseUrl ?? "",
    capabilities: null,
    capabilityKey:
      typeof existing?.capabilityMapping?.key === "string" ? existing.capabilityMapping.key : "",
    capabilityNames: Array.isArray(existing?.capabilityMapping?.names)
      ? (existing.capabilityMapping.names as string[]).map(String)
      : [],
    capabilitySource:
      typeof existing?.capabilityMapping?.source === "string"
        ? existing.capabilityMapping.source
        : "observation_name",
    confirmed: initialConfirmed(existing),
    credentialId: existing?.id ?? null,
    customTimeRange: range.customTimeRange,
    error: null,
    name: existing?.name ?? defaultName,
    pollIntervalSeconds: existing?.pollIntervalSeconds ?? 300,
    projects: [],
    sourceProjectId: cfg?.sourceProjectId ?? "",
    targetProjectId: cfg?.targetProjectId ?? projectId,
    timeRange: range.timeRange,
    verified: Boolean(existing?.verifiedAt),
  };
}

function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "PATCH": {
      const next = { ...state, ...action.patch };
      // Editing credentials invalidates the connect confirm.
      if (action.patch.verified === false) {
        next.confirmed = { ...next.confirmed, connect: false };
      }
      return next;
    }
    case "CONFIRM":
      return {
        ...state,
        confirmed: { ...state.confirmed, [action.section]: true },
      };
    case "RESET":
      return initialState(action.projectId, action.existing, action.defaultName ?? "");
  }
}

function SectionStatusDot({ done, step }: { done: boolean; step: number }) {
  if (done) {
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-sm bg-success/15 text-success">
        <Icon.success className="size-4" />
      </span>
    );
  }
  return (
    <span className="flex size-7 shrink-0 items-center justify-center rounded-sm border-2 border-dashed border-border text-xs font-semibold text-muted-foreground">
      {step}
    </span>
  );
}

function SectionHint({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={text}
          className="pointer-events-auto shrink-0 rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          type="button"
        >
          <Icon.help className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{text}</TooltipContent>
    </Tooltip>
  );
}

function SectionCard({
  step,
  title,
  summary,
  hint,
  icon: IconComponent,
  done,
  forceOpen,
  sectionRef,
  children,
}: {
  step: number;
  title: string;
  summary: string;
  /** Detail worth a hover but not a line in the body. */
  hint?: string;
  icon: typeof Icon.dataset;
  done: boolean;
  forceOpen?: boolean;
  sectionRef?: (el: HTMLDivElement | null) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(!done || Boolean(forceOpen));

  useEffect(() => {
    if (forceOpen) setOpen(true);
    else setOpen(!done);
  }, [done, forceOpen]);

  return (
    <div
      className={cn(
        "overflow-hidden rounded-md border bg-card transition-colors",
        done ? "border-success/40" : "border-border"
      )}
      ref={sectionRef}
    >
      <div className="relative transition-colors hover:bg-wash-raised">
        {/* Stretched so the row still toggles while the hint stays a real button
            beside the title, rather than one nested inside another. */}
        <button
          aria-expanded={open}
          aria-label={title}
          className="absolute inset-0"
          onClick={() => setOpen((v) => !v)}
          type="button"
        />
        <div className="pointer-events-none relative flex w-full items-center gap-3 px-4 py-3.5 text-left">
          <SectionStatusDot done={done} step={step} />
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-foreground">
            <IconComponent className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p
              className={cn(
                "flex items-center gap-1.5 text-sm font-semibold",
                done ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {title}
              {hint ? <SectionHint text={hint} /> : null}
            </p>
            <p className={cn(PROSE, "mt-0.5 text-xs text-muted-foreground")}>{summary}</p>
          </div>
          {done ? (
            <Badge className="gap-1" variant="success">
              <Icon.success className="size-3" />
              Done
            </Badge>
          ) : (
            <Icon.chevronDown
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180"
              )}
            />
          )}
        </div>
      </div>
      {open && <div className="border-t border-border/70 p-4">{children}</div>}
    </div>
  );
}

export interface ConnectorSetupWizardProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  meta: ConnectorMeta;
  /** Edit / continue setup for an existing credential. */
  existing?: ConnectorCredential;
  /** Expand/scroll to a section when opening (e.g. filters from the visibility panel). */
  initialStep?: SetupSection;
  onCompleted?: (info: { credentialId: string; targetProjectId: string }) => void;
}

export function ConnectorSetupWizard({
  open,
  onClose,
  projectId,
  meta,
  existing,
  initialStep,
  onCompleted,
}: ConnectorSetupWizardProps) {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(wizardReducer, undefined, () =>
    initialState(projectId, existing, meta.label)
  );
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const sectionEls = useRef<Partial<Record<SetupSection, HTMLDivElement | null>>>({});
  // Credential rows without a sync config are drafts — discard on abandon.
  // Editing a finished connection never discards (hadConfigOnOpen).
  const isEdit = Boolean(existing?.activeConfig);
  const hadConfigOnOpen = isEdit;
  const completedRef = useRef(false);
  const discardInFlightRef = useRef(false);

  const patch = (p: Partial<WizardState>) => dispatch({ patch: p, type: "PATCH" });
  const confirmSection = (section: SetupSection) => dispatch({ section, type: "CONFIRM" });

  useEffect(() => {
    if (!open) return;
    dispatch({ defaultName: meta.label, existing, projectId, type: "RESET" });
    setPreviewCount(null);
    setFinishing(false);
    completedRef.current = false;
    discardInFlightRef.current = false;
  }, [open, projectId, existing, meta.label]);

  // Jump to a section when opened from the visibility panel.
  useEffect(() => {
    if (!open || !initialStep) return;
    const t = window.setTimeout(() => {
      sectionEls.current[initialStep]?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    return () => window.clearTimeout(t);
  }, [open, initialStep]);

  // Existing credentials — load source projects once verified.
  useEffect(() => {
    if (!open || !state.credentialId || !state.verified || state.projects.length > 0) return;
    let cancelled = false;
    void api.connectorCredentials
      .connectorCredentialsSourceProjectsRetrieve({ id: state.credentialId })
      .then((res) => {
        if (cancelled) return;
        dispatch({
          patch: {
            projects: (res.projects ?? []).map((p) => ({
              id: p.id ?? "",
              name: p.name ?? p.id ?? "",
            })),
          },
          type: "PATCH",
        });
      })
      .catch(() => {
        /* Source section handles empty */
      });
    return () => {
      cancelled = true;
    };
  }, [open, state.credentialId, state.verified, state.projects.length]);

  const overmindProjectsQuery = useQuery({
    enabled: open,
    queryFn: () => api.projects.projectsList({ ordering: "name", pageSize: 100 }),
    queryKey: ["projects-for-import"],
  });

  const capabilitiesQuery = useQuery({
    enabled: open && !!state.targetProjectId && state.verified,
    queryFn: () =>
      api.capabilities.capabilitiesList({
        ordering: "name",
        pageSize: 100,
        project: state.targetProjectId,
      }),
    queryKey: ["capabilities-for-connector-wizard", state.targetProjectId],
  });

  const lookbackDays = lookbackDaysFromRange(state.timeRange, state.customTimeRange);
  const backfillWindow = useMemo(
    () => resolveTimeRange(state.timeRange, state.customTimeRange),
    [state.timeRange, state.customTimeRange]
  );
  const { backfillFrom, backfillTo } = useMemo(
    () => ({
      backfillFrom: backfillWindow.gte ? new Date(backfillWindow.gte) : undefined,
      backfillTo: backfillWindow.lte ? new Date(backfillWindow.lte) : undefined,
    }),
    [backfillWindow]
  );

  // The sync config is only written when setup finishes, so a needs_source_project
  // adapter can only learn the chosen project from here.
  const discoverySourceProject =
    (state.capabilities?.needsSourceProject ?? true) ? state.sourceProjectId : "";
  const discoveryReady =
    open &&
    !!state.credentialId &&
    state.verified &&
    (!(state.capabilities?.needsSourceProject ?? true) || !!state.sourceProjectId);

  const candidatesQuery = useQuery({
    enabled: discoveryReady,
    queryFn: () =>
      api.connectorCredentials.connectorCredentialsDiscoverCapabilitiesRetrieve({
        id: state.credentialId!,
        lookbackDays: lookbackDays ?? 30,
        sourceProjectId: discoverySourceProject || undefined,
      }),
    queryKey: [
      "connector-discover-capabilities",
      state.credentialId,
      lookbackDays,
      discoverySourceProject,
    ],
  });

  // Values live behind a chosen key, so they need their own scoped pass.
  const metadataValuesQuery = useQuery({
    enabled: discoveryReady && state.capabilitySource === "metadata" && !!state.capabilityKey,
    queryFn: () =>
      api.connectorCredentials.connectorCredentialsDiscoverCapabilitiesRetrieve({
        id: state.credentialId!,
        key: state.capabilityKey,
        lookbackDays: lookbackDays ?? 30,
        source: "metadata",
        sourceProjectId: discoverySourceProject || undefined,
      }),
    queryKey: [
      "connector-discover-metadata-values",
      state.credentialId,
      lookbackDays,
      state.capabilityKey,
      discoverySourceProject,
    ],
  });

  // Prefill from the repo scan, leaving anything already mapped alone.
  useEffect(() => {
    const proposed = candidatesQuery.data?.proposals;
    if (!proposed || state.confirmed.capabilities) return;
    const next = { ...state.assignments };
    let added = false;
    for (const [key, proposal] of Object.entries(proposed)) {
      if (!next[key] && proposal.capabilityId) {
        next[key] = proposal.capabilityId;
        added = true;
      }
    }
    if (added) dispatch({ patch: { assignments: next }, type: "PATCH" });
  }, [candidatesQuery.data, state.assignments, state.confirmed.capabilities]);

  // Live preview count once credentials verify.
  useEffect(() => {
    if (!open || !state.credentialId || !state.verified || !discoveryReady) {
      setPreviewCount(null);
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(() => {
      setPreviewLoading(true);
      void api.connectorCredentials
        .connectorCredentialsPreviewCreate({
          connectorPreviewRequestRequest: {
            backfillFrom,
            backfillTo,
            lookbackDays: lookbackDays ?? undefined,
            sourceProjectId: discoverySourceProject || undefined,
          },
          id: state.credentialId!,
        })
        .then((res) => {
          if (!cancelled) setPreviewCount(res.count ?? null);
        })
        .catch(() => {
          if (!cancelled) setPreviewCount(null);
        })
        .finally(() => {
          if (!cancelled) setPreviewLoading(false);
        });
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [
    open,
    state.credentialId,
    state.verified,
    discoveryReady,
    discoverySourceProject,
    lookbackDays,
    backfillFrom,
    backfillTo,
  ]);

  const saveConnectMutation = useMutation({
    mutationFn: async () => {
      if (state.credentialId) {
        await api.connectorCredentials.connectorCredentialsPartialUpdate({
          id: state.credentialId,
          patchedConnectorCredentialRequest: {
            baseUrl: state.baseUrl,
            name: state.name,
            ...(state.apiKey ? { apiKey: state.apiKey } : {}),
            ...(state.apiSecret ? { apiSecret: state.apiSecret } : {}),
          },
        });
        return state.credentialId;
      }
      const created = await api.connectorCredentials.connectorCredentialsCreate({
        connectorCredentialRequest: {
          apiKey: state.apiKey,
          apiSecret: state.apiSecret,
          autoSyncEnabled: false,
          baseUrl: state.baseUrl,
          connectorType: meta.type as ConnectorCredential["connectorType"],
          name: state.name,
          project: projectId,
        },
      });
      return created.id;
    },
    onError: (e: Error) => patch({ error: e.message }),
  });

  const verifyMutation = useMutation({
    mutationFn: async (credentialId: string) => {
      const res = await api.connectorCredentials.connectorCredentialsVerifyCreate({
        id: credentialId,
      });
      if (!res.ok) throw new Error(res.detail || "Verification failed");
      return res;
    },
    onError: (e: Error) => patch({ error: e.message, verified: false }),
    onSuccess: (res) => {
      const caps = res.capabilities ?? null;
      const capabilitySources = caps?.capabilitySources ?? [];
      const nextSource =
        capabilitySources.length > 0 && !capabilitySources.includes(state.capabilitySource)
          ? capabilitySources[0]
          : state.capabilitySource;
      patch({
        apiVersion: res.apiVersion ?? null,
        capabilities: caps,
        capabilitySource: nextSource,
        error: null,
        projects: (res.projects ?? []).map((p) => ({ id: p.id ?? "", name: p.name ?? p.id ?? "" })),
        verified: true,
      });
      confirmSection("connect");
      // Providers that don't need a source project skip that section.
      if (caps && !caps.needsSourceProject) {
        confirmSection("source");
      }
      void qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
    },
  });

  async function handleVerify() {
    patch({ error: null });
    try {
      const id = await saveConnectMutation.mutateAsync();
      patch({ credentialId: id });
      await verifyMutation.mutateAsync(id);
    } catch {
      // errors patched in mutations
    }
  }

  async function discardDraft(credentialId: string | null) {
    if (!credentialId || hadConfigOnOpen || completedRef.current) return;
    if (discardInFlightRef.current) return;
    discardInFlightRef.current = true;
    try {
      await api.connectorCredentials.connectorCredentialsDestroy({ id: credentialId });
      void qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
    } catch {
      // Best-effort — draft stays hidden from the list until config exists.
    }
  }

  async function finish() {
    if (!state.credentialId) return;
    setFinishing(true);
    patch({ error: null });
    try {
      // Persist setup first (no auto-kick from config — /sync owns the start).
      await api.connectorCredentials.connectorCredentialsConfigCreate({
        connectorSyncConfigWriteRequest: {
          backfillFrom,
          backfillTo,
          lookbackDays: lookbackDaysFromRange(state.timeRange, state.customTimeRange),
          sourceProjectId: state.sourceProjectId,
          targetProjectId: state.targetProjectId,
        },
        id: state.credentialId,
      });

      await api.connectorCredentials.connectorCredentialsCapabilityMappingUpdate({
        connectorCapabilityMappingWriteRequest: {
          assignments: state.assignments,
          autoCreate: false,
          key: state.capabilityKey || null,
          names: state.capabilityNames,
          source: state.capabilitySource as ConnectorCapabilityMappingWriteRequestSourceEnum,
        },
        id: state.credentialId,
      });

      // Kick sync while auto-sync is still off so perform_update doesn't race.
      await api.connectorCredentials.connectorCredentialsSyncCreate({ id: state.credentialId });

      await api.connectorCredentials.connectorCredentialsPartialUpdate({
        id: state.credentialId,
        patchedConnectorCredentialRequest: {
          autoSyncEnabled: state.autoSyncEnabled,
          pollIntervalSeconds: state.pollIntervalSeconds,
        },
      });

      completedRef.current = true;
      void qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
      onCompleted?.({
        credentialId: state.credentialId,
        targetProjectId: state.targetProjectId,
      });
      onClose();
    } catch (e) {
      patch({ error: e instanceof Error ? e.message : "Setup failed" });
    } finally {
      setFinishing(false);
    }
  }

  const needsSourceProject = state.capabilities?.needsSourceProject ?? true;
  const capabilitySourceOptions = useMemo(() => {
    const allowed = state.capabilities?.capabilitySources;
    const supported = allowed?.length
      ? CAPABILITY_SOURCES.filter((s) => allowed.includes(s.value))
      : [...CAPABILITY_SOURCES];
    return supported.filter((s) => s.boundary || s.value === state.capabilitySource);
  }, [state.capabilities?.capabilitySources, state.capabilitySource]);

  const connectDone = state.verified && state.confirmed.connect;
  const sourceValid = !needsSourceProject || Boolean(state.sourceProjectId);
  const sourceDone =
    connectDone && (!needsSourceProject || (state.confirmed.source && sourceValid));
  const rangeValid =
    state.timeRange !== "custom" || Boolean(state.customTimeRange.gte || state.customTimeRange.lte);
  const rangeDone = connectDone && state.confirmed.filters && rangeValid;
  const capabilitiesDone = connectDone && state.confirmed.capabilities;
  const destinationDone =
    connectDone && state.confirmed.destination && Boolean(state.targetProjectId);

  const prereqStates = needsSourceProject
    ? [connectDone, sourceDone, rangeDone, capabilitiesDone, destinationDone]
    : [connectDone, rangeDone, capabilitiesDone, destinationDone];
  const completedCount = prereqStates.filter(Boolean).length;
  const totalCount = prereqStates.length;
  const allDone = prereqStates.every(Boolean);

  const busy = saveConnectMutation.isPending || verifyMutation.isPending || finishing;
  const canVerify = Boolean(state.name && (state.credentialId || state.apiKey));

  async function handleClose() {
    if (busy) return;
    const draftId = state.credentialId;
    onClose();
    // Terminate incomplete setup so a half-verified key never becomes an integration.
    await discardDraft(draftId);
  }

  const blockedReason = !connectDone
    ? "Verify credentials first"
    : needsSourceProject && !sourceDone
      ? "Confirm the source project"
      : !rangeDone
        ? "Confirm the import range"
        : !capabilitiesDone
          ? "Confirm capability mapping"
          : !destinationDone
            ? "Confirm the destination"
            : null;

  const capabilities = capabilitiesQuery.data?.results ?? [];
  const allCandidates = candidatesQuery.data?.candidates ?? [];
  const shapes = candidatesQuery.data?.shapes ?? [];
  const discoveryFailure = candidatesQuery.error ?? metadataValuesQuery.error;
  const discoveryError = discoveryFailure ? errorMessage(discoveryFailure) : undefined;
  const discovered = allCandidates.filter(
    (c): c is ConnectorCapabilityCandidate & { value: string } => {
      if (!c.value) return false;
      if (c.source && c.source !== state.capabilitySource) return false;
      return true;
    }
  );
  // Chosen boundary names are the rows to map; nothing discovers them for you.
  const candidates =
    state.capabilitySource === "observation_name"
      ? state.capabilityNames.map((name) => ({
          count: shapes.find((s) => s.name === name)?.traces ?? 0,
          value: name,
        }))
      : state.capabilitySource === "metadata"
        ? (metadataValuesQuery.data?.candidates ?? []).filter(
            (c): c is ConnectorCapabilityCandidate & { value: string } => Boolean(c.value)
          )
        : discovered;
  const otherSources = Array.from(
    new Set(
      allCandidates
        .filter((c) => c.value && c.source && c.source !== state.capabilitySource)
        .filter((c) => c.source !== "observation_name")
        .map((c) => c.source as string)
        // Against every supported source, not the visible list: reaching a
        // demoted trace-wide source is the point of this hint.
        .filter((s) =>
          (
            state.capabilities?.capabilitySources ?? CAPABILITY_SOURCES.map((o) => o.value)
          ).includes(s)
        )
    )
  );
  const metadataKeys = (allCandidates.find((c) => c.source === "metadata" && !c.value)
    ?.metadataKeys ?? []) as MetadataKey[];
  const proposals = candidatesQuery.data?.proposals ?? {};
  const overmindProjects = overmindProjectsQuery.data?.results ?? [];

  const bindSection = (key: SetupSection) => (el: HTMLDivElement | null) => {
    sectionEls.current[key] = el;
  };

  let step = 1;

  return (
    <Dialog onOpenChange={(v) => !v && void handleClose()} open={open}>
      <DialogContent size="lg">
        <DialogHeader>
          <div className="flex min-w-0 items-start gap-3">
            {meta.logoUrl && (
              <img alt="" className="mt-0.5 size-8 shrink-0 rounded-md" src={meta.logoUrl} />
            )}
            <div className="min-w-0">
              <DialogTitle>
                {isEdit ? `Edit ${meta.label} connection` : `Connect ${meta.label}`}
              </DialogTitle>
              <DialogDescription className="mt-0.5">
                {isEdit
                  ? "Update credentials, range, and capability mapping."
                  : "Credentials, range, and capability mapping on one page."}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <DialogBody className="min-w-0 space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-xs font-medium text-muted-foreground">Setup</p>
              <Badge className="tabular-nums" variant={allDone ? "default" : "secondary"}>
                {completedCount} / {totalCount} ready
              </Badge>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-xs bg-muted">
              <div
                className="h-full rounded-xs bg-success transition-all duration-500"
                style={{ width: `${(completedCount / totalCount) * 100}%` }}
              />
            </div>
          </div>

          <div className="space-y-3">
            <SectionCard
              done={connectDone}
              forceOpen={initialStep === "connect"}
              icon={Icon.lock}
              sectionRef={bindSection("connect")}
              step={step++}
              summary={`API keys for your ${meta.label} project.`}
              title="Credentials"
            >
              <ConnectBody
                apiKey={state.apiKey}
                apiSecret={state.apiSecret}
                baseUrl={state.baseUrl}
                busy={saveConnectMutation.isPending || verifyMutation.isPending}
                canVerify={canVerify}
                hasCredential={Boolean(state.credentialId)}
                meta={meta}
                name={state.name}
                onChange={patch}
                onVerify={() => void handleVerify()}
                verified={state.verified}
              />
            </SectionCard>

            {needsSourceProject ? (
              <SectionCard
                done={sourceDone}
                forceOpen={initialStep === "source"}
                icon={Icon.folder}
                sectionRef={bindSection("source")}
                step={step++}
                summary={`Which ${meta.label} project to pull traces from.`}
                title="Source Project"
              >
                {connectDone ? (
                  <SourceBody
                    label={meta.label}
                    onSelect={(id) => {
                      patch({ sourceProjectId: id });
                      confirmSection("source");
                    }}
                    projects={state.projects}
                    selectedId={state.sourceProjectId}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">Verify credentials first.</p>
                )}
              </SectionCard>
            ) : null}

            <SectionCard
              done={rangeDone}
              forceOpen={initialStep === "filters"}
              icon={Icon.history}
              sectionRef={bindSection("filters")}
              step={step++}
              summary="How far back to import on first sync."
              title="Import Range"
            >
              {connectDone ? (
                <RangeBody
                  confirmed={rangeDone}
                  customTimeRange={state.customTimeRange}
                  exactCount={state.capabilities?.exactCount ?? true}
                  onChange={patch}
                  onConfirm={() => confirmSection("filters")}
                  previewCount={previewCount}
                  previewLoading={previewLoading}
                  retentionNote={state.capabilities?.retentionNote ?? ""}
                  timeRange={state.timeRange}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Verify credentials first.</p>
              )}
            </SectionCard>

            <SectionCard
              done={capabilitiesDone}
              forceOpen={initialStep === "capabilities"}
              hint={state.capabilitySource === "observation_name" ? BOUNDARY_HINT : undefined}
              icon={Icon.capability}
              sectionRef={bindSection("capabilities")}
              step={step++}
              summary={`Map ${meta.label} identities to Overmind capabilities (optional).`}
              title="Capability Mapping"
            >
              {connectDone && discoveryReady ? (
                <CapabilitiesBody
                  assignments={state.assignments}
                  candidates={candidates}
                  capabilities={capabilities.map((a) => ({ id: a.id, label: a.name }))}
                  capabilityKey={state.capabilityKey}
                  capabilityNames={state.capabilityNames}
                  capabilitySource={state.capabilitySource}
                  capabilitySources={capabilitySourceOptions}
                  confirmed={capabilitiesDone}
                  error={discoveryError}
                  loading={candidatesQuery.isLoading || metadataValuesQuery.isLoading}
                  lookbackDays={candidatesQuery.data?.lookbackDays ?? lookbackDays}
                  metadataKeys={metadataKeys}
                  onChange={patch}
                  onConfirm={() => confirmSection("capabilities")}
                  otherSources={otherSources}
                  proposals={proposals}
                  rangeLabel={rangeSummary(state.timeRange, state.customTimeRange)}
                  sampled={candidatesQuery.data?.sampled}
                  shapes={shapes}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  {state.verified ? "Choose a source project first." : "Verify credentials first."}
                </p>
              )}
            </SectionCard>

            <SectionCard
              done={destinationDone}
              forceOpen={initialStep === "destination"}
              icon={Icon.project}
              sectionRef={bindSection("destination")}
              step={step++}
              summary="Where traces land in Overmind, and whether to keep polling."
              title="Destination"
            >
              {connectDone ? (
                <DestinationBody
                  autoSyncEnabled={state.autoSyncEnabled}
                  confirmed={destinationDone}
                  onChange={patch}
                  onConfirm={() => confirmSection("destination")}
                  pollIntervalSeconds={state.pollIntervalSeconds}
                  projects={overmindProjects.map((p) => ({ id: p.id, name: p.name }))}
                  targetProjectId={state.targetProjectId}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Verify credentials first.</p>
              )}
            </SectionCard>
          </div>

          {state.error && (
            <div className="flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive/5 p-3">
              <Icon.warning className="mt-0.5 size-4 shrink-0 text-destructive" />
              <p className="text-sm text-destructive">{state.error}</p>
            </div>
          )}
        </DialogBody>

        <DialogFooter className="gap-2">
          {blockedReason && (
            <p className="mr-auto text-xs text-muted-foreground">{blockedReason}</p>
          )}
          <Button disabled={busy} onClick={() => void handleClose()} variant="secondary">
            <Icon.close />
            Cancel
          </Button>
          <Button
            disabled={!allDone || busy}
            onClick={() => void finish()}
            title={blockedReason ?? undefined}
          >
            {finishing ? <Spinner className="text-current" size="sm" /> : null}
            {finishing
              ? isEdit
                ? "Saving…"
                : "Starting…"
              : isEdit
                ? "Confirm and sync"
                : "Start sync"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConnectBody({
  name,
  baseUrl,
  apiKey,
  apiSecret,
  hasCredential,
  verified,
  canVerify,
  busy,
  meta,
  onChange,
  onVerify,
}: {
  name: string;
  baseUrl: string;
  apiKey: string;
  apiSecret: string;
  hasCredential: boolean;
  verified: boolean;
  canVerify: boolean;
  busy: boolean;
  meta: ConnectorMeta;
  onChange: (p: Partial<WizardState>) => void;
  onVerify: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="lf-name">
          Connection name
        </Label>
        <Input
          id="lf-name"
          onChange={(e) => onChange({ name: e.target.value, verified: false })}
          value={name}
        />
      </div>
      {meta.needsBaseUrl ? (
        <div>
          <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="lf-url">
            {meta.baseUrlLabel ?? "Host URL"}
            {meta.baseUrlHint ? (
              <span className="ml-1 text-muted-foreground/50">{meta.baseUrlHint}</span>
            ) : null}
          </Label>
          <Input
            id="lf-url"
            onChange={(e) => onChange({ baseUrl: e.target.value, verified: false })}
            placeholder={meta.baseUrlPlaceholder}
            value={baseUrl}
          />
        </div>
      ) : null}
      <div>
        <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="lf-key">
          {meta.apiKeyLabel}
          {hasCredential && (
            <span className="ml-1 text-muted-foreground/50">(leave blank to keep current)</span>
          )}
        </Label>
        <Input
          autoComplete="off"
          className="font-mono"
          id="lf-key"
          onChange={(e) => onChange({ apiKey: e.target.value, verified: false })}
          placeholder={meta.apiKeyPlaceholder}
          type="password"
          value={apiKey}
        />
      </div>
      {meta.needsSecret ? (
        <div>
          <Label className="mb-1.5 block text-sm text-muted-foreground" htmlFor="lf-secret">
            {meta.secretLabel}
            {hasCredential && (
              <span className="ml-1 text-muted-foreground/50">(leave blank to keep current)</span>
            )}
          </Label>
          <Input
            autoComplete="off"
            className="font-mono"
            id="lf-secret"
            onChange={(e) => onChange({ apiSecret: e.target.value, verified: false })}
            placeholder={meta.secretPlaceholder}
            type="password"
            value={apiSecret}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!canVerify || busy || verified} onClick={onVerify} size="sm">
          {busy ? <Spinner className="text-current" size="sm" /> : null}
          {busy ? "Verifying…" : verified ? "Verified" : "Verify credentials"}
        </Button>
        <a
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          href={meta.docsUrl}
          rel="noreferrer"
          target="_blank"
        >
          <Icon.externalLink className="size-4" />
          Where to find your credentials
        </a>
      </div>
      {verified ? <p className="text-xs text-success">Credentials verified.</p> : null}
    </div>
  );
}

function SourceBody({
  projects,
  selectedId,
  label,
  onSelect,
}: {
  projects: { id: string; name: string }[];
  selectedId: string;
  label: string;
  onSelect: (id: string) => void;
}) {
  if (projects.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <p className={`${PROSE} text-sm text-muted-foreground`}>
          No {label} source projects returned for this key.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <SelectableCardGroup className="grid gap-2 sm:grid-cols-2">
        {projects.map((p) => (
          <SelectableCard
            key={p.id}
            onSelect={() => onSelect(p.id)}
            role="radio"
            selected={selectedId === p.id}
          >
            <div className="px-3 py-2.5">
              <div className="text-sm font-medium">{p.name || p.id}</div>
              <div className="font-mono text-xs text-muted-foreground">{p.id}</div>
            </div>
          </SelectableCard>
        ))}
      </SelectableCardGroup>
    </div>
  );
}

function RangeBody({
  timeRange,
  customTimeRange,
  previewCount,
  previewLoading,
  exactCount,
  retentionNote,
  confirmed,
  onChange,
  onConfirm,
}: {
  timeRange: TimeRangePreset;
  customTimeRange: { gte?: string; lte?: string };
  previewCount: number | null;
  previewLoading: boolean;
  exactCount: boolean;
  retentionNote: string;
  confirmed: boolean;
  onChange: (p: Partial<WizardState>) => void;
  onConfirm: () => void;
}) {
  const rangeOk = timeRange !== "custom" || Boolean(customTimeRange.gte || customTimeRange.lte);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label className="mb-1.5 block text-sm text-muted-foreground">Import from</Label>
        <TimeRangeButton
          customTimeRange={customTimeRange}
          onChange={(preset, custom) =>
            onChange({
              customTimeRange: custom ?? (preset === "custom" ? customTimeRange : {}),
              timeRange: preset,
            })
          }
          value={timeRange}
        />
      </div>
      <p className="text-sm text-muted-foreground">
        {previewLoading
          ? "Counting…"
          : previewCount == null
            ? "Preview unavailable"
            : exactCount
              ? `${previewCount.toLocaleString()} traces in range`
              : `~${previewCount.toLocaleString()} traces in range`}
      </p>
      {retentionNote ? (
        <p className={`${PROSE} text-sm text-muted-foreground`}>{retentionNote}</p>
      ) : null}
      {!confirmed && (
        <Button disabled={!rangeOk} onClick={onConfirm} size="sm" variant="secondary">
          <Icon.success />
          Confirm range
        </Button>
      )}
    </div>
  );
}

function proposalEvidence(proposal: ConnectorCapabilityProposal) {
  if (proposal.method === "source") return proposal.evidence;
  if (proposal.method === "name") return `a matching name in ${proposal.evidence}`;
  return "its capability card";
}

/** Keys as discovered, plus a saved key the current sample no longer contains. */
function metadataKeyOptions(keys: MetadataKey[], capabilityKey: string): MetadataKey[] {
  if (!capabilityKey || keys.some((k) => k.name === capabilityKey)) return keys;
  return [{ coverage: 0, name: capabilityKey, observations: 0 }, ...keys];
}

function MetadataKeyWarning({
  keys,
  capabilityKey,
}: {
  keys: MetadataKey[];
  capabilityKey: string;
}) {
  const chosen = keys.find((k) => k.name === capabilityKey);
  if (!chosen || chosen.coverage < 1) return null;
  return (
    <p className={`${PROSE} text-sm text-muted-foreground`}>
      <span className="font-mono">{chosen.name}</span> is on every span, so each one would start its
      own trace. Only a key set on the capability span alone can mark a boundary.
    </p>
  );
}

type ShapeNode = { shape: ConnectorShapeCandidate; children: ShapeNode[] };

/**
 * Nest the sampled shapes by their most common parent. The result is the app's
 * usual tree rather than any one trace's: a name whose parent left the sample,
 * or whose parent chain loops, roots a branch of its own.
 */
export function buildShapeTree(shapes: ConnectorShapeCandidate[]): ShapeNode[] {
  const best = new Map<string, ConnectorShapeCandidate>();
  for (const shape of shapes) {
    const prior = best.get(shape.name);
    if (!prior || shape.score > prior.score) best.set(shape.name, shape);
  }

  const terminates = (name: string) => {
    const seen = new Set([name]);
    let parent = best.get(name)?.parentName ?? null;
    while (parent) {
      if (seen.has(parent)) return false;
      seen.add(parent);
      parent = best.get(parent)?.parentName ?? null;
    }
    return true;
  };

  const nodes = new Map<string, ShapeNode>();
  for (const shape of best.values()) nodes.set(shape.name, { children: [], shape });

  const roots: ShapeNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.shape.parentName ? nodes.get(node.shape.parentName) : undefined;
    if (parent && parent !== node && terminates(node.shape.name)) parent.children.push(node);
    else roots.push(node);
  }

  const sort = (list: ShapeNode[]) => {
    list.sort((a, b) => b.shape.score - a.shape.score || a.shape.name.localeCompare(b.shape.name));
    for (const node of list) sort(node.children);
  };
  sort(roots);
  return roots;
}

export function descendantNames(node: ShapeNode, into = new Set<string>()): Set<string> {
  for (const child of node.children) {
    into.add(child.shape.name);
    descendantNames(child, into);
  }
  return into;
}

/** Ancestors of every node satisfying *hit*, so those nodes are on screen. */
function ancestorsOf(tree: ShapeNode[], hit: (node: ShapeNode) => boolean, into: Set<string>) {
  const walk = (node: ShapeNode, trail: string[]): boolean => {
    // Every branch is walked: `some` would stop at the first match and leave a
    // second matching subtree closed.
    const found = node.children.map((child) => walk(child, [...trail, node.shape.name]));
    if (hit(node) || found.some(Boolean)) {
      for (const name of trail) into.add(name);
      return true;
    }
    return false;
  };
  for (const node of tree) walk(node, []);
  return into;
}

export function ancestorsOfSelected(tree: ShapeNode[], selected: string[]): Set<string> {
  const wanted = new Set(selected);
  return ancestorsOf(tree, (node) => wanted.has(node.shape.name), new Set());
}

/**
 * Names to render for *query*: the matches themselves plus the ancestors that
 * place them, so a match never appears detached from the pick that claims it.
 */
export function namesMatching(tree: ShapeNode[], query: string): Set<string> {
  const needle = query.trim().toLowerCase();
  if (!needle) return new Set();
  const matches = (node: ShapeNode) => node.shape.name.toLowerCase().includes(needle);
  const shown = new Set<string>();
  const collect = (nodes: ShapeNode[]) => {
    for (const node of nodes) {
      if (matches(node)) shown.add(node.shape.name);
      collect(node.children);
    }
  };
  collect(tree);
  return ancestorsOf(tree, matches, shown);
}

function ShapeRow({
  node,
  depth,
  rails,
  isLast,
  lockedBy,
  capabilityNames,
  capabilityLabel,
  onToggle,
  isExpanded,
  onExpand,
  visible,
}: {
  node: ShapeNode;
  depth: number;
  /** Whether each ancestor above the immediate parent still has siblings below. */
  rails: boolean[];
  isLast: boolean;
  lockedBy: string | null;
  capabilityNames: string[];
  capabilityLabel: (name: string) => string | null;
  onToggle: (node: ShapeNode) => void;
  isExpanded: (name: string) => boolean;
  onExpand: (name: string, expanded: boolean) => void;
  /** Null while unfiltered; otherwise the names a search left on screen. */
  visible: Set<string> | null;
}) {
  const { shape } = node;
  const shownChildren = visible
    ? node.children.filter((child) => visible.has(child.shape.name))
    : node.children;
  const selected = capabilityNames.includes(shape.name);
  const included = lockedBy !== null && !selected;
  const conflicting = lockedBy !== null && selected;
  const nested = descendantNames(node).size;
  const claimedBy = included ? capabilityLabel(lockedBy) : null;
  const line = included ? "bg-primary/40" : "bg-border/60";
  const expanded = shownChildren.length > 0 && isExpanded(shape.name);

  const note = included
    ? `included in ${lockedBy}${claimedBy ? ` · ${claimedBy}` : ""}`
    : conflicting
      ? `also splitting out of ${lockedBy} — untick it to fold this back in`
      : selected && nested > 0
        ? `roots its own trace, with ${nested} nested observation${nested === 1 ? "" : "s"}`
        : shape.reasons.join(" · ");

  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {included && <Icon.success aria-hidden className="size-3 shrink-0 text-primary" />}
        <span className="font-mono text-sm">{shape.name}</span>
        <Badge variant="outline">{shape.type}</Badge>
        <span className="text-xs text-muted-foreground">{shape.traces} traces</span>
        {nested > 0 && !expanded && (
          <span className="text-xs text-muted-foreground">+{nested} nested</span>
        )}
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">{note}</div>
    </>
  );

  return (
    <>
      <div className="flex items-stretch">
        {rails.map((continues, level) => (
          <span className="relative w-4 shrink-0" key={level}>
            {continues && <span className={cn("absolute inset-y-0 left-2 w-px", line)} />}
          </span>
        ))}
        {depth > 0 && (
          <span className="relative w-4 shrink-0">
            <span
              className={cn("absolute top-0 left-2 w-px", line)}
              style={{ height: isLast ? "50%" : "100%" }}
            />
            <span className={cn("absolute top-1/2 left-2 h-px w-2", line)} />
          </span>
        )}
        <span className="flex w-5 shrink-0 items-center justify-center">
          {shownChildren.length > 0 && (
            <button
              aria-expanded={expanded}
              aria-label={`${expanded ? "Collapse" : "Expand"} ${shape.name}`}
              className="rounded-sm p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              onClick={() => onExpand(shape.name, !expanded)}
              type="button"
            >
              {expanded ? (
                <Icon.chevronDown className="size-3.5" />
              ) : (
                <Icon.chevronRight className="size-3.5" />
              )}
            </button>
          )}
        </span>
        {included ? (
          // Not actionable: its capability follows the ancestor that claimed it.
          <div
            className={cn(
              selectableCardVariants({ selected: false }),
              "flex-1 border-primary/25 bg-primary/5 px-3 py-2"
            )}
          >
            {body}
          </div>
        ) : (
          <SelectableCard
            className="flex-1 px-3 py-2"
            onSelect={() => onToggle(node)}
            selected={selected}
          >
            {body}
          </SelectableCard>
        )}
      </div>
      {expanded &&
        shownChildren.map((child, index) => (
          <ShapeRow
            capabilityLabel={capabilityLabel}
            capabilityNames={capabilityNames}
            depth={depth + 1}
            isExpanded={isExpanded}
            isLast={index === shownChildren.length - 1}
            key={child.shape.name}
            lockedBy={lockedBy ?? (selected ? shape.name : null)}
            node={child}
            onExpand={onExpand}
            onToggle={onToggle}
            rails={depth === 0 ? [] : [...rails, !isLast]}
            visible={visible}
          />
        ))}
    </>
  );
}

function BoundaryShapes({
  shapes,
  capabilityNames,
  assignments,
  capabilities,
  onChange,
}: {
  shapes: ConnectorShapeCandidate[];
  capabilityNames: string[];
  assignments: Record<string, string>;
  capabilities: { id: string; label: string }[];
  onChange: (p: Partial<WizardState>) => void;
}) {
  const tree = buildShapeTree(shapes);
  const [query, setQuery] = useState("");
  // Deep trees are mostly generations nobody picks, so start collapsed — but open
  // the path to every pick so a saved mapping is visible without hunting.
  const openByDefault = useMemo(
    () => ancestorsOfSelected(tree, capabilityNames),
    [tree, capabilityNames]
  );
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const filtering = Boolean(query.trim());
  const visible = useMemo(
    () => (filtering ? namesMatching(tree, query) : null),
    [tree, query, filtering]
  );
  const isExpanded = (name: string) =>
    filtering ? true : (overrides[name] ?? openByDefault.has(name));
  const onExpand = (name: string, expanded: boolean) =>
    setOverrides((prior) => ({ ...prior, [name]: expanded }));

  const capabilityLabel = (name: string) =>
    capabilities.find((a) => a.id === assignments[name])?.label ?? null;

  // Choosing an ancestor claims its subtree, so any boundary inside it is dropped.
  const toggle = (node: ShapeNode) => {
    const name = node.shape.name;
    if (capabilityNames.includes(name)) {
      onChange({ capabilityNames: capabilityNames.filter((n) => n !== name) });
      return;
    }
    const claimed = descendantNames(node);
    onChange({
      assignments: Object.fromEntries(
        Object.entries(assignments).filter(([key]) => !claimed.has(key))
      ),
      capabilityNames: [...capabilityNames.filter((n) => !claimed.has(n)), name],
    });
  };

  // A saved name can outlive the shape that suggested it, once the window moves.
  const unsampled = capabilityNames.filter(
    (name) =>
      !shapes.some((s) => s.name === name) &&
      (!filtering || name.toLowerCase().includes(query.trim().toLowerCase()))
  );
  const roots = visible ? tree.filter((node) => visible.has(node.shape.name)) : tree;

  return (
    <div className="flex flex-col gap-2">
      <SearchInput
        label="Filter observation names"
        onChange={(e) => setQuery(e.target.value)}
        onClear={() => setQuery("")}
        placeholder="Filter observation names"
        size="sm"
        value={query}
      />
      <div className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
        {filtering && roots.length === 0 && (
          <p className="px-1 py-2 text-sm text-muted-foreground">
            No observation name contains "{query.trim()}".
          </p>
        )}
        {unsampled.map((name) => (
          <SelectableCard
            className="px-3 py-2"
            key={name}
            onSelect={() =>
              onChange({ capabilityNames: capabilityNames.filter((n) => n !== name) })
            }
            selected={true}
          >
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-sm">{name}</span>
              <span className="text-xs text-muted-foreground">not seen in this sample</span>
            </div>
          </SelectableCard>
        ))}
        {roots.map((node, index) => (
          <ShapeRow
            capabilityLabel={capabilityLabel}
            capabilityNames={capabilityNames}
            depth={0}
            isExpanded={isExpanded}
            isLast={index === roots.length - 1}
            key={node.shape.name}
            lockedBy={null}
            node={node}
            onExpand={onExpand}
            onToggle={toggle}
            rails={[]}
            visible={visible}
          />
        ))}
      </div>
    </div>
  );
}

function CapabilitiesBody({
  capabilitySource,
  capabilityKey,
  capabilitySources,
  candidates,
  capabilities,
  assignments,
  error,
  loading,
  lookbackDays,
  rangeLabel,
  sampled,
  otherSources,
  proposals,
  shapes,
  capabilityNames,
  metadataKeys,
  confirmed,
  onChange,
  onConfirm,
}: {
  capabilitySource: string;
  capabilityKey: string;
  capabilityNames: string[];
  capabilitySources: readonly { value: string; label: string }[];
  candidates: ConnectorCapabilityCandidate[];
  capabilities: { id: string; label: string }[];
  assignments: Record<string, string>;
  error?: string;
  loading: boolean;
  lookbackDays: number | null;
  rangeLabel: string;
  sampled?: number;
  otherSources: string[];
  proposals: Record<string, ConnectorCapabilityProposal>;
  shapes: ConnectorShapeCandidate[];
  metadataKeys: MetadataKey[];
  confirmed: boolean;
  onChange: (p: Partial<WizardState>) => void;
  onConfirm: () => void;
}) {
  const sourceLabel = (s: string) =>
    capabilitySources.find((x) => x.value === s)?.label ??
    CAPABILITY_SOURCES.find((x) => x.value === s)?.label ??
    s;
  const needsKey = capabilitySource === "metadata" && !capabilityKey;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label className="mb-1.5 block text-sm text-muted-foreground">Identity Source</Label>
        <Tabs
          className="w-auto"
          onValueChange={(next) => onChange({ assignments: {}, capabilitySource: next })}
          value={capabilitySource}
        >
          <TabsList>
            {capabilitySources.map((s) => (
              <TabsTrigger key={s.value} value={s.value}>
                {s.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      {capabilitySource === "metadata" && (
        <div>
          <Label className="mb-1.5 block text-sm text-muted-foreground">Metadata Key</Label>
          <Select
            onValueChange={(next) => onChange({ assignments: {}, capabilityKey: next })}
            value={capabilityKey || undefined}
          >
            <SelectTrigger className="w-[240px]">
              <SelectValue placeholder="Choose a key" />
            </SelectTrigger>
            <SelectContent>
              {metadataKeyOptions(metadataKeys, capabilityKey).map((k) => (
                <SelectItem key={k.name} value={k.name}>
                  <span className="font-mono">{k.name}</span>
                  {k.observations > 0 && (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {Math.round(k.coverage * 100)}% of spans
                    </span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {capabilitySource === "metadata" && (
        <MetadataKeyWarning capabilityKey={capabilityKey} keys={metadataKeys} />
      )}

      {/* Without this the sample request's failure reads as "this app has no capabilities". */}
      {error && (
        <div className="flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive/5 p-3">
          <Icon.warning className="mt-0.5 size-4 shrink-0 text-destructive" />
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {capabilitySource === "observation_name" && !loading && !error && (
        <BoundaryShapes
          assignments={assignments}
          capabilities={capabilities}
          capabilityNames={capabilityNames}
          onChange={onChange}
          shapes={shapes}
        />
      )}
      {(capabilitySource === "tag" || capabilitySource === "trace_name") && (
        <p className={`${PROSE} text-sm text-muted-foreground`}>
          {sourceLabel(capabilitySource)} labels a whole trace, so runs arrive intact rather than
          split per capability. Switch to{" "}
          <button
            className="text-foreground underline-offset-2 hover:underline"
            onClick={() => onChange({ assignments: {}, capabilitySource: "observation_name" })}
            type="button"
          >
            observation names
          </button>{" "}
          to root traces at the capability.
        </p>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner size="sm" /> Discovering…
        </div>
      )}
      {!loading && !error && needsKey && (
        <p className="text-sm text-muted-foreground">
          Choose a metadata key to list the values it takes.
        </p>
      )}
      {!loading &&
        !error &&
        !needsKey &&
        candidates.length === 0 &&
        capabilitySource !== "observation_name" && (
          <div className="flex flex-col gap-2 text-sm text-muted-foreground">
            <p>
              No candidates for {rangeLabel}
              {sampled != null ? ` (${sampled} traces sampled)` : ""}
              {lookbackDays != null ? ` · ~${lookbackDays}d` : ""} for{" "}
              {sourceLabel(capabilitySource)}.
            </p>
            {shapes.length > 0 && (
              <p>
                This app may not declare its capabilities.{" "}
                <button
                  className="text-foreground underline-offset-2 hover:underline"
                  onClick={() =>
                    onChange({ assignments: {}, capabilitySource: "observation_name" })
                  }
                  type="button"
                >
                  Choose a boundary by observation name
                </button>{" "}
                instead.
              </p>
            )}
            {otherSources.length > 0 ? (
              <p>
                Found values under{" "}
                {otherSources.map((s, i) => (
                  <span key={s}>
                    {i > 0 ? ", " : ""}
                    <button
                      className="text-foreground underline-offset-2 hover:underline"
                      onClick={() => onChange({ assignments: {}, capabilitySource: s })}
                      type="button"
                    >
                      {sourceLabel(s)}
                    </button>
                  </span>
                ))}
                .
              </p>
            ) : (
              <p>Leave unmapped — traces import with no capability assigned.</p>
            )}
          </div>
        )}
      {candidates.length > 0 && (
        <div className="flex flex-col gap-2">
          {candidates.map((c) => {
            const value = c.value!;
            const proposal = proposals[value];
            const suggested = proposal && assignments[value] === proposal.capabilityId;
            return (
              <div
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border/70 px-3 py-2"
                key={value}
              >
                <div className="min-w-0">
                  <div className="truncate font-mono text-sm">{value}</div>
                  <div className="text-xs text-muted-foreground">
                    {c.count ?? 0} traces
                    {suggested ? ` · suggested from ${proposalEvidence(proposal)}` : ""}
                  </div>
                </div>
                <Select
                  onValueChange={(capabilityId) => {
                    const next = { ...assignments };
                    if (capabilityId === "__none__") delete next[value];
                    else next[value] = capabilityId;
                    onChange({ assignments: next });
                  }}
                  value={assignments[value] ?? "__none__"}
                >
                  <SelectTrigger className="w-[220px]">
                    <SelectValue placeholder="Unmapped" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Unmapped</SelectItem>
                    {capabilities.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        {a.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            );
          })}
        </div>
      )}
      {!confirmed && (
        <Button onClick={onConfirm} size="sm" variant="secondary">
          <Icon.success />
          {Object.keys(assignments).length > 0 ? "Confirm mapping" : "Continue without mapping"}
        </Button>
      )}
    </div>
  );
}

function DestinationBody({
  targetProjectId,
  autoSyncEnabled,
  pollIntervalSeconds,
  projects,
  confirmed,
  onChange,
  onConfirm,
}: {
  targetProjectId: string;
  autoSyncEnabled: boolean;
  pollIntervalSeconds: number;
  projects: { id: string; name: string }[];
  confirmed: boolean;
  onChange: (p: Partial<WizardState>) => void;
  onConfirm: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Label className="mb-1.5 block text-sm text-muted-foreground">
          Target Overmind project
        </Label>
        <Select onValueChange={(id) => onChange({ targetProjectId: id })} value={targetProjectId}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center justify-between gap-3 rounded-md border border-border/70 px-3 py-2.5">
        <div>
          <p className="text-sm font-medium">Auto-sync</p>
          <p className="text-xs text-muted-foreground">Poll for new traces after setup.</p>
        </div>
        <div className="flex items-center gap-3">
          {autoSyncEnabled && (
            <PollIntervalSelect
              onChange={(v) => onChange({ pollIntervalSeconds: v })}
              seconds={pollIntervalSeconds}
            />
          )}
          <Switch
            checked={autoSyncEnabled}
            onCheckedChange={(v) => onChange({ autoSyncEnabled: v })}
          />
        </div>
      </div>
      {!confirmed && (
        <Button disabled={!targetProjectId} onClick={onConfirm} size="sm" variant="secondary">
          <Icon.success />
          Confirm destination
        </Button>
      )}
    </div>
  );
}
