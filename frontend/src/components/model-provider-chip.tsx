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
import { Icon as SystemIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export { getModelProviderInfo } from "@/components/model-provider";

type ProviderIcon = React.ComponentType<{ className?: string; size?: number | string }>;

/** @lobehub/icons ships no TypeSafe mark. */
function TypeSafe({ className, size = "1em" }: { className?: string; size?: number | string }) {
  return (
    <svg
      aria-hidden
      className={className}
      data-testid="logo-typesafe"
      fill="currentColor"
      height={size}
      viewBox="0 0 67 67"
      width={size}
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M0 0 C0.4 0.1 0.4 0.1 2.5 0.8 C3.3 1 4.1 1.3 4.9 1.5 C7.7 2.7 9.5 3.5 11.5 5.8 C12.1 8.8 11.9 11.8 11.5 14.9 C11.3 16.6 11.2 18.1 11.5 19.8 C13.3 20.8 14.6 21.3 16.5 21.8 C19.1 22.6 20.8 23.6 22.5 25.8 C23.7 30.3 23.9 34.7 23.9 39.4 C23.9 40.1 23.9 40.7 24 41.4 C24 51.7 24 51.7 20.9 56 C16.9 59.4 12.4 61.4 7.5 63.1 C5.7 63.7 4.3 64.5 2.8 65.6 C0.5 66.8 0.5 66.8 -2 66.6 C-6.6 65.1 -10.4 63.5 -13.5 59.8 C-14.1 56.9 -13.9 54.2 -13.5 51.3 C-13.3 49.6 -13.3 49.6 -13.5 46.8 C-15.3 45.8 -16.6 45.3 -18.5 44.8 C-21.2 43.9 -22.8 43 -24.5 40.8 C-25.7 36.3 -25.9 31.8 -25.9 27.2 C-26 26.8 -26 26.8 -26 25.2 C-26 14.9 -26 14.9 -22.9 10.6 C-18.9 7.2 -14.4 5.2 -9.5 3.5 C-7.7 2.9 -6.3 2.1 -4.8 1 C-2.5 -0.2 -2.5 -0.2 0 0 Z M-19.9 12.8 C-24.1 18 -22.7 26 -22.1 32.3 C-21.9 33.8 -21.7 35.3 -21.5 36.8 C-18.6 35.8 -16 34.6 -13.5 32.8 C-12.5 30.3 -12.7 28.2 -13 25.5 C-13 24.9 -13.1 24.2 -13.1 23.6 C-13.2 22 -13.4 20.4 -13.5 18.8 C-12 18.3 -10.6 17.8 -9.1 17.3 C-8.8 17.2 -8.8 17.2 -7.5 16.8 C-7 16.6 -6.5 16.5 -6 16.3 C-4.6 15.7 -4.6 15.7 -2.5 13.8 C-2.1 10.8 -2.2 7.8 -2.5 4.8 C-8.5 4.8 -15.3 9.1 -19.9 12.8 Z M0.5 4.8 C-0 7.6 -0.2 10 0.5 12.8 C2.9 15 5.5 16.4 8.5 17.8 C8.8 14.7 9 11.9 8.5 8.8 C6.8 6.8 4.9 5.7 2.5 4.8 C1.8 4.8 1.2 4.8 0.5 4.8 Z M-5.2 17.7 C-5.8 18.1 -6.4 18.5 -7.1 18.9 C-7.6 19.2 -8 19.5 -8.5 19.8 C-4.8 23.7 -4.8 23.7 -2.5 24.8 C1 24.4 3.6 22.7 6.5 20.8 C5.7 20.1 5 19.5 4.2 18.9 C3.7 18.5 3.3 18.1 2.9 17.8 C-0.4 15.5 -1.8 15.6 -5.2 17.7 Z M-10.5 23.8 C-11 26.9 -11.2 29.7 -10.5 32.8 C-8.1 34.8 -5.5 35.8 -2.5 36.8 C-2.2 33.7 -2 30.9 -2.5 27.8 C-4.2 25.8 -6.1 24.7 -8.5 23.8 C-9.2 23.8 -9.8 23.8 -10.5 23.8 Z M1.8 26.1 C-0.1 28.6 0.2 31.1 0.2 34.2 C0.2 36.5 0.2 38.6 -0.5 40.8 C-2.7 42.6 -4.5 43.6 -7 44.7 C-8.3 45.3 -8.3 45.3 -10.5 46.8 C-11.4 49.9 -11.1 52.6 -10.5 55.8 C-8.7 54.9 -6.8 54 -4.9 53 C-4.4 52.8 -3.9 52.5 -3.4 52.3 C-2.3 51.7 -1.3 51.2 -0.2 50.6 C1.5 49.8 1.5 49.8 3.4 49.1 C5.6 48.2 6.9 47.5 8.5 45.8 C9.4 42 9.1 38.4 8.9 34.5 C8.9 33.5 8.9 32.5 8.8 31.4 C8.7 28.9 8.6 26.3 8.5 23.8 C5.7 23.8 4.1 24.5 1.8 26.1 Z M11.5 23.8 C11.3 27.2 11.2 30.6 11.1 34 C11.1 35 11 36 11 37 C10.8 44 10.8 44 12.8 46.4 C15.1 47.9 16.7 48.8 19.5 48.8 C19.7 45.4 19.8 42 19.9 38.5 C19.9 37.6 20 36.6 20 35.6 C20.2 28.6 20.2 28.6 18.2 26.2 C15.9 24.7 14.3 23.8 11.5 23.8 Z M-16.7 37.3 C-17 37.4 -17 37.4 -18.3 38.1 C-18.7 38.4 -19.1 38.6 -19.5 38.8 C-17.4 41.2 -16.1 42.6 -13.1 43.7 C-9.6 43.8 -8.3 42.8 -5.5 40.8 C-5.8 39.4 -5.8 39.4 -6.5 37.8 C-10.2 35.5 -12.9 35.3 -16.7 37.3 Z M5.5 49.8 C4.8 50.3 4 50.7 3.2 51.2 C2.8 51.4 2.8 51.4 0.9 52.6 C0.1 53.1 -0.7 53.5 -1.5 54 C-7.4 57.5 -7.4 57.5 -8.5 59.8 C-6 61.7 -3.6 63.1 -0.5 63.8 C3.1 62.7 5.9 60.7 9 58.5 C9.8 58 10.6 57.5 11.5 56.9 C13.5 55.6 15.5 54.2 17.5 52.8 C16.7 52.1 16 51.5 15.2 50.9 C14.7 50.5 14.3 50.1 13.9 49.8 C10.5 47.4 9.1 47.7 5.5 49.8 Z"
        transform="translate(34.51171875,0.20703125)"
      />
    </svg>
  );
}

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
  typesafe: TypeSafe,
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
  showProvider?: boolean;
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
  showProvider = true,
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
      {showProvider ? (
        <>
          <ProviderLogo
            Icon={Icon}
            providerLabel={info.providerLabel}
            providerSlug={info.providerSlug}
          />
          <span className="shrink-0">{info.providerLabel}</span>
        </>
      ) : (
        <SystemIcon.model className="size-3.5 shrink-0" />
      )}
      {showModelId && (
        <span className="min-w-0 truncate text-muted-foreground">{info.modelLabel}</span>
      )}
      {children}
    </Badge>
  );
}
