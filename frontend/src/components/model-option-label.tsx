import type { ReactNode } from "react";

import { getModelProviderInfo } from "@/components/model-provider";
import { getProviderIcon, ProviderLogo } from "@/components/model-provider-chip";
import { cn } from "@/lib/utils";

export function modelOptionName(model: string, name?: string) {
  return name?.replace(/^[^:]+:\s+/, "") || getModelProviderInfo(model).modelLabel;
}

export function ModelOptionLabel({
  model,
  baseModel,
  name,
  children,
  className,
}: {
  model: string;
  baseModel?: string;
  name?: string;
  children?: ReactNode;
  className?: string;
}) {
  const info = getModelProviderInfo(baseModel || model);
  const label = name || (baseModel ? `${info.modelLabel} · FT` : undefined);
  return (
    <span className={cn("flex min-w-0 flex-1 items-center gap-2", className)} title={model}>
      <span aria-hidden="true" className="flex shrink-0 text-current">
        <ProviderLogo
          Icon={getProviderIcon(info.id)}
          providerLabel={info.providerLabel}
          providerSlug={info.providerSlug}
        />
      </span>
      <span className="min-w-0 truncate">{modelOptionName(model, label)}</span>
      {children}
    </span>
  );
}
