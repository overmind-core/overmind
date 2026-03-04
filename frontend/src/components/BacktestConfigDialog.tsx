import { useMemo, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ResponseError, type ModelInfo } from "@/api";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// ---------------------------------------------------------------------------
// Types & helpers
// ---------------------------------------------------------------------------

type ModelConfig = {
  baseSelected: boolean;
  reasoningSelected: boolean;
  reasoningEffort: string;
};

const DEFAULT_CONFIG: ModelConfig = {
  baseSelected: false,
  reasoningSelected: false,
  reasoningEffort: "medium",
};

function modelKeysForConfig(model: ModelInfo, config: ModelConfig): string[] {
  const keys: string[] = [];
  const { modelName, reasoningRequired, adaptiveMode } = model;

  if (reasoningRequired) {
    if (config.baseSelected) keys.push(modelName);
    return keys;
  }

  if (config.baseSelected) keys.push(modelName);

  if (config.reasoningSelected) {
    if (adaptiveMode === false) {
      keys.push(`${modelName}:reasoning`);
    } else {
      keys.push(`${modelName}:reasoning-${config.reasoningEffort || "medium"}`);
    }
  }

  return keys;
}

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Gemini",
};

const PROVIDER_ORDER = ["openai", "anthropic", "gemini"];

// ---------------------------------------------------------------------------
// ModelRow
// ---------------------------------------------------------------------------

interface ModelRowProps {
  model: ModelInfo;
  config: ModelConfig;
  onChange: (update: Partial<ModelConfig>) => void;
}

function ModelRow({ model, config, onChange }: ModelRowProps) {
  const { modelName, supportsReasoning, adaptiveMode, reasoningLevels, reasoningRequired } = model;

  return (
    <div className="flex items-start gap-3 rounded-md px-2 py-2 hover:bg-muted/40 transition-colors">
      <Checkbox
        id={`base-${modelName}`}
        checked={config.baseSelected}
        onCheckedChange={(v) => onChange({ baseSelected: !!v })}
        className="mt-0.5 shrink-0"
      />
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <label htmlFor={`base-${modelName}`} className="text-sm font-medium cursor-pointer">
            {modelName}
          </label>
          {reasoningRequired && (
            <Badge variant="secondary" className="text-xs">reasoning always on</Badge>
          )}
        </div>

        {supportsReasoning && !reasoningRequired && (
          <div className="flex items-center gap-2 flex-wrap">
            <Checkbox
              id={`reasoning-${modelName}`}
              checked={config.reasoningSelected}
              onCheckedChange={(v) => onChange({ reasoningSelected: !!v })}
              className="size-3.5 shrink-0"
            />
            <label
              htmlFor={`reasoning-${modelName}`}
              className="text-xs text-muted-foreground cursor-pointer"
            >
              w/ reasoning
            </label>

            {config.reasoningSelected && adaptiveMode === true && reasoningLevels.length > 0 && (
              <Select
                value={config.reasoningEffort}
                onValueChange={(v) => onChange({ reasoningEffort: v })}
              >
                <SelectTrigger size="sm" className="h-6 text-xs w-auto min-w-[80px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {reasoningLevels.map((level) => (
                    <SelectItem key={level} value={level} className="text-xs">
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {config.reasoningSelected && adaptiveMode === false && (
              <span className="text-xs text-muted-foreground italic">budget tokens</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BacktestConfigDialog
// ---------------------------------------------------------------------------

interface BacktestConfigDialogProps {
  promptId: string;
  onSuccess: () => void;
}

export function BacktestConfigDialog({ promptId, onSuccess }: BacktestConfigDialogProps) {
  const [open, setOpen] = useState(false);
  const [configs, setConfigs] = useState<Record<string, ModelConfig>>({});
  const queryClient = useQueryClient();

  const { data: models = [], isLoading } = useQuery({
    queryKey: ["backtesting-models"],
    queryFn: () => apiClient.backtesting.listAvailableModelsApiV1BacktestingModelsGet({}),
    staleTime: 5 * 60 * 1000,
    enabled: open,
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

  const backtestMutation = useMutation({
    mutationFn: () =>
      apiClient.backtesting
        .runBacktestingApiV1BacktestingRunPost({
          backtestingRequest: { promptId, models: allModelKeys },
        })
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Backtesting trigger failed");
          }
          throw error;
        }),
    onSuccess: () => {
      setOpen(false);
      setConfigs({});
      queryClient.invalidateQueries({ queryKey: ["agent-detail"] });
      onSuccess();
    },
    onError: (error: Error) => {
      toast.error(error.message);
    },
  });

  const handleOpenChange = (v: boolean) => {
    if (!v) setConfigs({});
    setOpen(v);
  };

  const orderedProviders = PROVIDER_ORDER.filter((p) => modelsByProvider[p]);
  const extraProviders = Object.keys(modelsByProvider).filter((p) => !PROVIDER_ORDER.includes(p));

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
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
            Select models and reasoning settings to compare against the current prompt.
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
                        key={model.modelName}
                        model={model}
                        config={configs[model.modelName] ?? DEFAULT_CONFIG}
                        onChange={(update) => updateConfig(model.modelName, update)}
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
          <p className="text-xs text-muted-foreground mb-2">
            {allModelKeys.length === 0
              ? "No models selected"
              : `${allModelKeys.length} configuration${allModelKeys.length !== 1 ? "s" : ""} selected`}
          </p>
          {allModelKeys.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {allModelKeys.map((key) => (
                <Badge key={key} variant="secondary" className="font-mono text-xs">
                  {key}
                </Badge>
              ))}
            </div>
          )}
        </div>

        <DialogFooter className="px-6 py-4 border-t shrink-0">
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
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
