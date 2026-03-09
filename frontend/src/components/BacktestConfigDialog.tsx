import { useMemo, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, FlaskConical, Info, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { type ModelInfo, ResponseError } from "@/api";
import apiClient from "@/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants, types & helpers
// ---------------------------------------------------------------------------

const MAX_MODEL_VARIANTS = 10;

export type ModelSuggestion = {
  model: string;
  provider: string;
  category: string;
  reasoning_effort: string | null;
  reason: string;
};

const CATEGORY_LABELS: Record<string, string> = {
  best_overall: "Best overall",
  cheapest: "Cheapest",
  fastest: "Fastest",
  most_capable: "Most capable",
};

const CATEGORY_CLASSES: Record<string, string> = {
  best_overall: "bg-emerald-500 text-white",
  cheapest: "bg-amber-500 text-white",
  fastest: "bg-sky-500 text-white",
  most_capable: "bg-violet-500 text-white",
};

type ModelConfig = {
  /** "base" = no-reasoning base model; reasoning levels = effort-based variants.
   *  Multiple values → multiple test configurations for this model. */
  selectedVariants: string[];
};

const DEFAULT_CONFIG: ModelConfig = { selectedVariants: [] };

function modelKeysForConfig(model: ModelInfo, config: ModelConfig): string[] {
  const { modelName, reasoningRequired, adaptiveMode } = model;

  if (config.selectedVariants.length === 0) return [];

  // reasoning-required models: reasoning is implicit, but effort level can still be chosen
  if (reasoningRequired) {
    const keys: string[] = [];
    const seen = new Set<string>();
    for (const variant of config.selectedVariants) {
      const key = `${modelName}:reasoning-${variant}`;
      if (!seen.has(key)) {
        keys.push(key);
        seen.add(key);
      }
    }
    return keys;
  }

  const keys: string[] = [];
  const seen = new Set<string>();

  for (const variant of config.selectedVariants) {
    let key: string;
    if (variant === "base") {
      key = modelName;
    } else if (adaptiveMode === false) {
      key = `${modelName}:reasoning`; // budget-token mode: no effort level
    } else {
      key = `${modelName}:reasoning-${variant}`;
    }
    if (!seen.has(key)) {
      keys.push(key);
      seen.add(key);
    }
  }

  return keys;
}

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  gemini: "Gemini",
  openai: "OpenAI",
};

const PROVIDER_ORDER = ["openai", "anthropic", "gemini"];

function capLabel(value: string): string {
  if (value === "on") return "On";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

// ---------------------------------------------------------------------------
// VariantPicker — multi-select dropdown
// ---------------------------------------------------------------------------

interface VariantPickerProps {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (next: string[]) => void;
  atMax: boolean;
  recommendedVariants?: Set<string>;
}

function VariantPicker({
  options,
  selected,
  onChange,
  atMax,
  recommendedVariants: _recommendedVariants,
}: VariantPickerProps) {
  function toggle(value: string) {
    const isActive = selected.includes(value);
    if (!isActive && atMax) return;
    onChange(isActive ? selected.filter((v) => v !== value) : [...selected, value]);
  }

  const triggerLabel =
    selected.length === 0
      ? "Select variants"
      : selected.length <= 2
        ? selected.map(capLabel).join(", ")
        : `${selected.length} selected`;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className={cn(
            "flex h-7 w-[158px] shrink-0 items-center justify-between gap-1 rounded-md border px-2.5",
            "border-input bg-background text-xs transition-colors",
            "hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            selected.length === 0 ? "text-muted-foreground" : "text-foreground"
          )}
          type="button"
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronDown className="size-3 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>

      <PopoverContent align="end" className="w-52 p-1">
        {options.map(({ value, label }) => {
          const isActive = selected.includes(value);
          const disabled = !isActive && atMax;
          return (
            <button
              className={cn(
                "flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-xs transition-colors text-left",
                "hover:bg-accent hover:text-accent-foreground",
                disabled && "cursor-not-allowed opacity-40"
              )}
              disabled={disabled}
              key={value}
              onClick={() => toggle(value)}
              type="button"
            >
              <div
                className={cn(
                  "flex size-3.5 shrink-0 items-center justify-center rounded-sm border transition-colors",
                  isActive ? "border-primary bg-primary text-primary-foreground" : "border-input"
                )}
              >
                {isActive && <Check className="size-2.5" />}
              </div>
              <span className="flex-1">{label}</span>
            </button>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// ModelRow
// ---------------------------------------------------------------------------

interface ModelRowProps {
  model: ModelInfo;
  config: ModelConfig;
  onChange: (update: Partial<ModelConfig>) => void;
  totalKeys: number;
  suggestions?: ModelSuggestion[];
}

function RecommendationBadges({ suggestions }: { suggestions: ModelSuggestion[] }) {
  if (!suggestions.length) return null;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge className="text-xs px-1.5 py-0 cursor-default bg-violet-500/20 text-violet-400 border-violet-500/30 hover:bg-violet-500/20 flex items-center gap-1">
            AI recommended
            <Info className="size-3" />
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-72 text-xs leading-snug p-3" side="top">
          <div className="space-y-2">
            {suggestions.map((s) => (
              <div key={s.category}>
                <span
                  className={cn(
                    "inline-block font-medium rounded px-1 py-0.5 mb-0.5",
                    CATEGORY_CLASSES[s.category] ?? "bg-muted text-muted-foreground"
                  )}
                >
                  {CATEGORY_LABELS[s.category] ?? s.category}
                </span>
                <p className="text-background/70">{s.reason}</p>
              </div>
            ))}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function ModelRow({ model, config, onChange, totalKeys, suggestions = [] }: ModelRowProps) {
  const { modelName, supportsReasoning, adaptiveMode, reasoningLevels, reasoningRequired, isNew } =
    model;
  const atMax = totalKeys >= MAX_MODEL_VARIANTS;

  const variantOptions = useMemo(() => {
    if (!supportsReasoning) return [];
    const levels = adaptiveMode === false ? ["on"] : (reasoningLevels ?? []);
    if (reasoningRequired) {
      return levels.map((l) => ({ label: capLabel(l), value: l }));
    }
    return [
      { label: "Base (no reasoning)", value: "base" },
      ...levels.map((l) => ({ label: capLabel(l), value: l })),
    ];
  }, [supportsReasoning, reasoningRequired, adaptiveMode, reasoningLevels]);

  // Map each suggestion's reasoning_effort to the corresponding variant value
  const recommendedVariants = useMemo(
    () => new Set(suggestions.map((s) => s.reasoning_effort ?? "base")),
    [suggestions]
  );

  // Only truly non-reasoning models use a plain checkbox
  if (variantOptions.length === 0) {
    const isChecked = config.selectedVariants.includes("base");
    return (
      <div className="flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-muted/40 transition-colors">
        <Checkbox
          checked={isChecked}
          className="shrink-0"
          disabled={!isChecked && atMax}
          id={`base-${modelName}`}
          onCheckedChange={(v) => onChange({ selectedVariants: v ? ["base"] : [] })}
        />
        <label
          className="flex-1 min-w-0 flex items-center gap-2 flex-wrap cursor-pointer"
          htmlFor={`base-${modelName}`}
        >
          <span className="text-sm font-medium">{modelName}</span>
          {isNew && (
            <Badge className="text-xs px-1.5 py-0 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/15">
              New
            </Badge>
          )}
          {reasoningRequired && (
            <Badge className="text-xs text-muted-foreground" variant="outline">
              reasoning always on
            </Badge>
          )}
          <RecommendationBadges suggestions={suggestions} />
        </label>
      </div>
    );
  }

  // Reasoning-capable models use the multi-select variant picker
  return (
    <div className="flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-muted/40 transition-colors">
      <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-medium">{modelName}</span>
        {isNew && (
          <Badge className="text-xs px-1.5 py-0 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/15">
            New
          </Badge>
        )}
        {reasoningRequired && (
          <Badge className="text-xs text-muted-foreground" variant="outline">
            reasoning always on
          </Badge>
        )}
        <RecommendationBadges suggestions={suggestions} />
      </div>
      <VariantPicker
        atMax={atMax}
        onChange={(variants) => onChange({ selectedVariants: variants })}
        options={variantOptions}
        recommendedVariants={recommendedVariants}
        selected={config.selectedVariants}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BacktestConfigDialog
// ---------------------------------------------------------------------------

interface BacktestConfigDialogProps {
  promptId: string;
  onSuccess: () => void;
  recommendations?: ModelSuggestion[];
}

export function BacktestConfigDialog({
  promptId,
  onSuccess,
  recommendations,
}: BacktestConfigDialogProps) {
  const [open, setOpen] = useState(false);
  const [configs, setConfigs] = useState<Record<string, ModelConfig>>({});
  const queryClient = useQueryClient();

  const { data: models = [], isLoading } = useQuery({
    enabled: open,
    queryFn: () => apiClient.backtesting.listAvailableModelsApiV1BacktestingModelsGet({}),
    queryKey: ["backtesting-models"],
    staleTime: 5 * 60 * 1000,
  });

  const modelsByProvider = useMemo(() => {
    const grouped: Record<string, ModelInfo[]> = {};
    for (const model of models) {
      if (!grouped[model.provider]) grouped[model.provider] = [];
      grouped[model.provider].push(model);
    }
    return grouped;
  }, [models]);

  const allModelKeys = useMemo(() => {
    const keys: string[] = [];
    for (const model of models) {
      const config = configs[model.modelName] ?? DEFAULT_CONFIG;
      keys.push(...modelKeysForConfig(model, config));
    }
    return keys;
  }, [models, configs]);

  const updateConfig = (modelName: string, update: Partial<ModelConfig>) => {
    setConfigs((prev) => ({
      ...prev,
      [modelName]: { ...(prev[modelName] ?? DEFAULT_CONFIG), ...update },
    }));
  };

  const removeKey = (key: string) => {
    let baseModel: string;
    let variantToRemove: string;
    if (key.includes(":reasoning-")) {
      const parts = key.split(":reasoning-");
      baseModel = parts[0] ?? "";
      variantToRemove = parts[1] ?? "";
    } else if (key.includes(":reasoning")) {
      baseModel = key.split(":reasoning")[0]!;
      variantToRemove = "on";
    } else {
      baseModel = key;
      variantToRemove = "base";
    }
    const current = configs[baseModel] ?? DEFAULT_CONFIG;
    updateConfig(baseModel, {
      selectedVariants: current.selectedVariants.filter((v) => v !== variantToRemove),
    });
  };

  const backtestMutation = useMutation({
    mutationFn: () =>
      apiClient.backtesting
        .runBacktestingApiV1BacktestingRunPost({
          backtestingRequest: { models: allModelKeys, promptId },
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Backtesting trigger failed");
          }
          throw error;
        }),
    onError: (error: Error) => {
      toast.error(error.message);
    },
    onSuccess: () => {
      setOpen(false);
      setConfigs({});
      queryClient.invalidateQueries({ queryKey: ["agent-detail"] });
      onSuccess();
    },
  });

  const handleOpenChange = (v: boolean) => {
    if (!v) setConfigs({});
    setOpen(v);
  };

  const orderedProviders = PROVIDER_ORDER.filter((p) => modelsByProvider[p]);
  const extraProviders = Object.keys(modelsByProvider).filter((p) => !PROVIDER_ORDER.includes(p));

  const atMax = allModelKeys.length >= MAX_MODEL_VARIANTS;

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <FlaskConical className="mr-1.5 size-3.5" />
          Backtest
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-xl flex flex-col max-h-[85vh] gap-0 p-0">
        <DialogHeader className="px-6 pt-6 pb-4 shrink-0">
          <DialogTitle>Configure Backtesting</DialogTitle>
          <DialogDescription>
            Select models and reasoning effort levels to compare. Each combination runs as a
            separate variant.
          </DialogDescription>
        </DialogHeader>

        {/* Scrollable model list */}
        <div className="flex-1 overflow-y-auto px-6 pb-2 min-h-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="space-y-5">
              {[...orderedProviders, ...extraProviders].map((provider) => (
                <div key={provider}>
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 px-2">
                    {PROVIDER_LABELS[provider] ?? provider}
                  </p>
                  <div className="divide-y divide-border/40">
                    {modelsByProvider[provider].map((model) => (
                      <ModelRow
                        config={configs[model.modelName] ?? DEFAULT_CONFIG}
                        key={model.modelName}
                        model={model}
                        onChange={(update) => updateConfig(model.modelName, update)}
                        suggestions={recommendations?.filter((r) => r.model === model.modelName)}
                        totalKeys={allModelKeys.length}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Selected summary */}
        <div className="px-6 py-3 border-t bg-muted/20 shrink-0">
          <div className="flex items-center justify-between mb-2">
            <p
              className={cn(
                "text-xs",
                atMax ? "text-amber-500 font-medium" : "text-muted-foreground"
              )}
            >
              {allModelKeys.length === 0
                ? "No variants selected"
                : atMax
                  ? `${allModelKeys.length} / ${MAX_MODEL_VARIANTS} variants — limit reached`
                  : `${allModelKeys.length} / ${MAX_MODEL_VARIANTS} variants selected`}
            </p>
          </div>
          {allModelKeys.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {allModelKeys.map((key) => (
                <Badge
                  className="font-mono text-xs pl-2 pr-1 flex items-center gap-1"
                  key={key}
                  variant="secondary"
                >
                  {key}
                  <button
                    aria-label={`Remove ${key}`}
                    className="rounded-full p-0.5 hover:bg-foreground/15 transition-colors"
                    onClick={() => removeKey(key)}
                    type="button"
                  >
                    <X className="size-2.5" />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </div>

        <DialogFooter className="px-6 py-4 border-t shrink-0">
          <Button onClick={() => handleOpenChange(false)} variant="outline">
            Cancel
          </Button>
          <Button
            disabled={allModelKeys.length === 0 || backtestMutation.isPending}
            onClick={() => backtestMutation.mutate()}
          >
            {backtestMutation.isPending ? (
              <Loader2 className="mr-1.5 size-4 animate-spin" />
            ) : (
              <FlaskConical className="mr-1.5 size-4" />
            )}
            Run Backtest
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
