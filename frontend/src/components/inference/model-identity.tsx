import { type MouseEvent, useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { primaryNameWithoutBase } from "@/components/inference/model-display-name";
import {
  getModelProviderInfo,
  getProviderIcon,
  ProviderLogo,
} from "@/components/model-provider-chip";
import { badgeVariants } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";

/** stopPropagation keeps a copy from also navigating the row. */
export function CopyableModelRef({ modelId }: { modelId: string }) {
  const [copied, setCopied] = useState(false);
  const copy = (e: MouseEvent) => {
    e.stopPropagation();
    void navigator.clipboard.writeText(modelId).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
        notify.success("Copied model id");
      },
      () => notify.error("Could not copy")
    );
  };
  return (
    <button
      className={cn(
        badgeVariants({ size: "default", variant: "outline" }),
        "max-w-full cursor-pointer gap-1.5 font-mono text-xs font-normal tracking-normal hover:bg-wash-raised"
      )}
      onClick={copy}
      title={`Copy ${modelId}`}
      type="button"
    >
      <span className="min-w-0 truncate">{modelId}</span>
      {copied ? (
        <Icon.success className="size-3.5 shrink-0 text-success" />
      ) : (
        <Icon.copy className="size-3.5 shrink-0 text-muted-foreground" />
      )}
    </button>
  );
}

/** `jobId` is typed non-null by the generated schema, but the API sends null for
    a model that was never trained here. */
export function TrainingJobChip({
  jobId,
  jobName,
  baseModelId,
  capabilityName,
  projectId,
}: {
  jobId: string | null | undefined;
  jobName?: string | null;
  baseModelId?: string;
  capabilityName?: string | null;
  projectId?: string;
}) {
  if (!jobId) return <span className="text-muted-foreground">—</span>;
  const stripped = baseModelId
    ? primaryNameWithoutBase(jobName, baseModelId, undefined, capabilityName)
    : jobName?.trim();
  const clean = stripped && stripped !== "Fine-tuned model" ? stripped : "";
  const label = clean || `job ${jobId.slice(0, 8)}`;
  return <EntityRef id={jobId} kind="job" name={label} projectId={projectId} />;
}

export function CapabilityChip({
  capabilityId,
  capabilityName,
}: {
  capabilityId: string;
  capabilityName?: string | null;
}) {
  return <EntityRef id={capabilityId} kind="capability" name={capabilityName} />;
}

export function BaseModelCell({ baseModelId }: { baseModelId: string }) {
  const info = getModelProviderInfo(baseModelId);
  const Logo = getProviderIcon(info.id);
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5" title={baseModelId}>
      <ProviderLogo
        Icon={Logo}
        providerLabel={info.providerLabel}
        providerSlug={info.providerSlug}
      />
      <span className="min-w-0 truncate text-sm">{info.modelLabel}</span>
    </span>
  );
}

export function FineTunedModelIdentity({
  baseModelId,
  displayName,
  capabilityName,
  modelId,
  size = "sm",
}: {
  baseModelId: string;
  displayName?: string | null;
  capabilityName?: string | null;
  modelId?: string | null;
  /** `lg` only where this model is the surface's whole subject. */
  size?: "sm" | "lg";
}) {
  const stripped = primaryNameWithoutBase(displayName, baseModelId, undefined, capabilityName);
  const clean = stripped && stripped !== "Fine-tuned model" ? stripped : "";
  const fallbackId = modelId?.trim() || "";
  const name = clean || fallbackId || "Fine-tuned model";
  const isId = !clean && !!fallbackId;
  const lg = size === "lg";

  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <Icon.overmind aria-hidden className="size-4 shrink-0" />
      <span
        className={cn(
          "min-w-0 truncate",
          isId && (lg ? "font-mono text-sm" : "font-mono text-xs"),
          !isId && (lg ? "text-base font-medium" : "text-sm font-medium")
        )}
      >
        {name}
      </span>
    </span>
  );
}
