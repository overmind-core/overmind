import { useState } from "react";

import Ai2 from "@lobehub/icons/es/Ai2/components/Mono";
import Ai21 from "@lobehub/icons/es/Ai21/components/Mono";
import Anthropic from "@lobehub/icons/es/Anthropic/components/Mono";
import Aws from "@lobehub/icons/es/Aws/components/Mono";
import Baichuan from "@lobehub/icons/es/Baichuan/components/Mono";
import Baidu from "@lobehub/icons/es/Baidu/components/Mono";
import ByteDance from "@lobehub/icons/es/ByteDance/components/Mono";
import Claude from "@lobehub/icons/es/Claude/components/Mono";
import Cohere from "@lobehub/icons/es/Cohere/components/Mono";
import Cursor from "@lobehub/icons/es/Cursor/components/Mono";
import DeepSeek from "@lobehub/icons/es/DeepSeek/components/Mono";
import Gemini from "@lobehub/icons/es/Gemini/components/Mono";
import Inception from "@lobehub/icons/es/Inception/components/Mono";
import Inflection from "@lobehub/icons/es/Inflection/components/Mono";
import Kimi from "@lobehub/icons/es/Kimi/components/Mono";
import Liquid from "@lobehub/icons/es/Liquid/components/Mono";
import Meta from "@lobehub/icons/es/Meta/components/Mono";
import Microsoft from "@lobehub/icons/es/Microsoft/components/Mono";
import Minimax from "@lobehub/icons/es/Minimax/components/Mono";
import Mistral from "@lobehub/icons/es/Mistral/components/Mono";
import NousResearch from "@lobehub/icons/es/NousResearch/components/Mono";
import Nvidia from "@lobehub/icons/es/Nvidia/components/Mono";
import OpenAI from "@lobehub/icons/es/OpenAI/components/Mono";
import OpenRouter from "@lobehub/icons/es/OpenRouter/components/Mono";
import Perplexity from "@lobehub/icons/es/Perplexity/components/Mono";
import Qwen from "@lobehub/icons/es/Qwen/components/Mono";
import Stepfun from "@lobehub/icons/es/Stepfun/components/Mono";
import Tencent from "@lobehub/icons/es/Tencent/components/Mono";
import Upstage from "@lobehub/icons/es/Upstage/components/Mono";
import XAI from "@lobehub/icons/es/XAI/components/Mono";
import XiaomiMiMo from "@lobehub/icons/es/XiaomiMiMo/components/Mono";
import Yi from "@lobehub/icons/es/Yi/components/Mono";
import Zhipu from "@lobehub/icons/es/Zhipu/components/Mono";

import { getModelProviderInfo, type ProviderId } from "@/components/model-provider";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export { getModelProviderInfo } from "@/components/model-provider";

type ProviderIcon = React.ComponentType<{ className?: string; size?: number | string }>;

const PROVIDER_ICONS: Record<Exclude<ProviderId, "unknown">, ProviderIcon> = {
  ai2: Ai2,
  ai21: Ai21,
  amazon: Aws,
  anthropic: Anthropic,
  baichuan: Baichuan,
  baidu: Baidu,
  bytedance: ByteDance,
  claude: Claude,
  cohere: Cohere,
  cursor: Cursor,
  deepseek: DeepSeek,
  google: Gemini,
  inception: Inception,
  inflection: Inflection,
  kimi: Kimi,
  liquid: Liquid,
  meta: Meta,
  microsoft: Microsoft,
  minimax: Minimax,
  mistral: Mistral,
  nous: NousResearch,
  nvidia: Nvidia,
  openai: OpenAI,
  openrouter: OpenRouter,
  perplexity: Perplexity,
  qwen: Qwen,
  stepfun: Stepfun,
  tencent: Tencent,
  upstage: Upstage,
  xai: XAI,
  xiaomi: XiaomiMiMo,
  yi: Yi,
  zhipu: Zhipu,
};

export const getProviderIcon = (id: ProviderId): ProviderIcon | undefined =>
  id === "unknown" ? undefined : PROVIDER_ICONS[id];

type ModelProviderChipProps = {
  model: string;
  className?: string;
  children?: React.ReactNode;
  showModelId?: boolean;
  compact?: boolean;
};

export function ProviderLogo({
  Icon,
  providerLabel,
  providerSlug,
}: {
  Icon?: ProviderIcon;
  providerLabel: string;
  providerSlug?: string;
}) {
  const [remoteFailed, setRemoteFailed] = useState(false);

  if (Icon) {
    return <Icon className="size-3.5" size={14} />;
  }

  if (providerSlug && !remoteFailed) {
    const remoteLogoUrl = `https://cdn.simpleicons.org/${providerSlug}`;

    return (
      <>
        <span
          aria-hidden="true"
          className="size-3.5 shrink-0 bg-current"
          style={{
            mask: `url("${remoteLogoUrl}") center / contain no-repeat`,
            WebkitMask: `url("${remoteLogoUrl}") center / contain no-repeat`,
          }}
          title={providerLabel}
        />
        <img
          alt=""
          aria-hidden="true"
          className="hidden"
          onError={() => setRemoteFailed(true)}
          src={remoteLogoUrl}
        />
      </>
    );
  }

  return (
    <svg aria-hidden="true" className="size-3.5" viewBox="0 0 24 24">
      <path d="M4 5h16v14H4V5Zm2 2v10h12V7H6Zm2 2h3v2H8V9Zm0 4h8v2H8v-2Z" fill="currentColor" />
    </svg>
  );
}

export function ModelProviderChip({
  model,
  className,
  children,
  showModelId = true,
  compact = false,
}: ModelProviderChipProps) {
  const info = getModelProviderInfo(model);
  const Icon = getProviderIcon(info.id);

  return (
    <Badge
      className={cn(
        "inline-flex min-w-0 max-w-full items-center gap-1.5 overflow-hidden border-border bg-wash-raised px-1.5 py-0.5 text-xs font-medium text-foreground",
        compact ? "h-6" : "h-7",
        className
      )}
      title={model}
      variant="outline"
    >
      <ProviderLogo
        Icon={Icon}
        providerLabel={info.providerLabel}
        providerSlug={info.providerSlug}
      />
      <span className="shrink-0">{info.providerLabel}</span>
      {showModelId && (
        <span className="min-w-0 truncate text-muted-foreground">{info.modelLabel}</span>
      )}
      {children}
    </Badge>
  );
}
