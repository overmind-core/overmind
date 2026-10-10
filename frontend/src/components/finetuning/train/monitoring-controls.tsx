import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Field } from "./field";

export type MonitoringOptions = {
  mode: "adaptive" | "steps" | "epoch" | "off";
  loss_sample: number;
  train_sample: number;
  target_seconds: number;
  overhead_fraction: number;
  max_checks: number;
  interval_steps: number | null;
  selection: "last" | "development_loss";
  early_stopping: { patience: number; min_delta: number; warmup_checks: number } | null;
};

export const defaultMonitoring: MonitoringOptions = {
  early_stopping: null,
  interval_steps: null,
  loss_sample: 2048,
  max_checks: 12,
  mode: "adaptive",
  overhead_fraction: 0.1,
  selection: "last",
  target_seconds: 300,
  train_sample: 256,
};

export function MonitoringControls({
  value,
  onChange,
}: {
  value: MonitoringOptions;
  onChange: (value: MonitoringOptions) => void;
}) {
  return (
    <div className="sm:col-span-2">
      <h4 className="text-sm font-medium">Development monitoring</h4>
      <p className="mt-1 text-xs text-muted-foreground">
        Initial check, periodic fixed-sample checks, verified checkpoints and full final development
        validation.
      </p>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer">Monitoring settings · {value.mode}</summary>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field htmlFor="monitor-mode" label="Schedule">
            <Select
              onValueChange={(mode: MonitoringOptions["mode"]) =>
                onChange({
                  ...value,
                  interval_steps: mode === "steps" ? 10 : null,
                  mode,
                  ...(mode === "off" ? { early_stopping: null, selection: "last" } : {}),
                })
              }
              value={value.mode}
            >
              <SelectTrigger id="monitor-mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="adaptive">Adaptive</SelectItem>
                <SelectItem value="steps">Every N steps</SelectItem>
                <SelectItem value="epoch">Every epoch</SelectItem>
                <SelectItem value="off">Off</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          {value.mode !== "off" && (
            <>
              {(
                [
                  ["loss_sample", "Development sample rows", 1, 1000000, 1],
                  ["train_sample", "Training reference rows", 0, 1000000, 1],
                  ["max_checks", "Maximum interim checks", 1, 100, 1],
                  ...(value.mode === "adaptive"
                    ? [
                        ["target_seconds", "Target interval (seconds)", 1, 86400, 1],
                        [
                          "overhead_fraction",
                          "Monitoring overhead target (fraction)",
                          0.001,
                          0.9,
                          0.01,
                        ],
                      ]
                    : []),
                  ...(value.mode === "steps"
                    ? [["interval_steps", "Steps between checks", 1, 100000000, 1]]
                    : []),
                ] as const
              ).map(([field, label, min, max, step]) => (
                <Field htmlFor={`monitor-${field}`} key={field} label={String(label)}>
                  <Input
                    id={`monitor-${field}`}
                    max={max}
                    min={min}
                    onChange={(event) =>
                      onChange({ ...value, [field]: event.target.valueAsNumber })
                    }
                    step={step}
                    type="number"
                    value={(value[field as keyof MonitoringOptions] as number) ?? ""}
                  />
                </Field>
              ))}
              <Field htmlFor="monitor-selection" label="Checkpoint selection">
                <Select
                  onValueChange={(selection: MonitoringOptions["selection"]) =>
                    onChange({ ...value, selection })
                  }
                  value={value.selection}
                >
                  <SelectTrigger id="monitor-selection">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="last">Last checkpoint</SelectItem>
                    <SelectItem value="development_loss">Lowest development loss</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <div className="flex items-center gap-2 sm:col-span-2">
                <Checkbox
                  checked={value.early_stopping !== null}
                  id="monitor-early-stop"
                  onCheckedChange={(checked) =>
                    onChange({
                      ...value,
                      early_stopping:
                        checked === true ? { min_delta: 0, patience: 3, warmup_checks: 2 } : null,
                    })
                  }
                />
                <Label htmlFor="monitor-early-stop">
                  Stop after development loss stops improving
                </Label>
              </div>
              {value.early_stopping &&
                (
                  [
                    ["patience", "Patience (checks)", 1, 1],
                    ["min_delta", "Minimum improvement", 0, 0.001],
                    ["warmup_checks", "Warm-up checks", 0, 1],
                  ] as const
                ).map(([field, label, min, step]) => (
                  <Field htmlFor={`monitor-${field}`} key={field} label={label}>
                    <Input
                      id={`monitor-${field}`}
                      min={min}
                      onChange={(event) =>
                        onChange({
                          ...value,
                          early_stopping: {
                            ...value.early_stopping!,
                            [field]: event.target.valueAsNumber,
                          },
                        })
                      }
                      step={step}
                      type="number"
                      value={value.early_stopping![field]}
                    />
                  </Field>
                ))}
              <p className="text-xs text-muted-foreground sm:col-span-2">
                Whole-group samples can exceed the target. Overhead is a scheduling target, not a
                spend limit. Task-level generation checks require an explicit scoring contract via
                MCP.
              </p>
            </>
          )}
        </div>
      </details>
    </div>
  );
}
