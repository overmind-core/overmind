import { type FormEvent, useState } from "react";

import type { CapabilityChoice } from "@/components/datasets/notebook/cell";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  cellsOf,
  type DatasetPatch,
  INTENT_LABEL,
  type Intent,
  intentOf,
} from "@/hooks/use-datasets";
import type { Dataset } from "@/openapi";

const NO_CAPABILITY = "none";
const INTENTS: Intent[] = ["train", "eval"];

export function DatasetSettings({
  dataset,
  capabilities,
  disabled,
  onSave,
}: {
  dataset: Dataset;
  capabilities: CapabilityChoice[];
  disabled: boolean;
  onSave: (patch: DatasetPatch) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [intent, setIntent] = useState<Intent>("pending");
  const intentFixed = cellsOf(dataset).some((cell) => cell.frozen);
  const [capability, setCapability] = useState(NO_CAPABILITY);

  const reset = (next: boolean) => {
    if (next) {
      setName(dataset.name ?? "");
      setIntent(intentOf(dataset));
      setCapability(dataset.capability ?? NO_CAPABILITY);
    }
    setOpen(next);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const patch: DatasetPatch = {};
    const trimmed = name.trim();
    if (trimmed && trimmed !== (dataset.name ?? "")) patch.name = trimmed;
    if (intent !== intentOf(dataset)) patch.intent = intent;
    const chosen = capability === NO_CAPABILITY ? null : capability;
    if (chosen !== (dataset.capability ?? null)) patch.capability = chosen;
    if (Object.keys(patch).length) onSave(patch);
    setOpen(false);
  };

  return (
    <Popover onOpenChange={reset} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label="Dataset settings"
          disabled={disabled}
          size="icon-sm"
          title="Dataset settings"
          variant="ghost"
        >
          <Icon.settings />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3">
        <form className="flex flex-col gap-3" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dataset-settings-name">Name</Label>
            <Input
              id="dataset-settings-name"
              maxLength={255}
              onChange={(event) => setName(event.target.value)}
              value={name}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dataset-settings-intent">Intent</Label>
            <Select
              disabled={intentFixed}
              onValueChange={(value) => setIntent(value as Intent)}
              value={intent === "pending" ? "" : intent}
            >
              <SelectTrigger className="w-full" id="dataset-settings-intent">
                <SelectValue placeholder={INTENT_LABEL.pending} />
              </SelectTrigger>
              <SelectContent>
                {INTENTS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {INTENT_LABEL[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {intentFixed && (
              <p className="text-xs text-muted-foreground">
                A version was used; the intent is fixed.
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dataset-settings-capability">Capability</Label>
            <Select onValueChange={setCapability} value={capability}>
              <SelectTrigger className="w-full" id="dataset-settings-capability">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_CAPABILITY}>None</SelectItem>
                {capabilities.map((choice) => (
                  <SelectItem key={choice.id} value={choice.id}>
                    {choice.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button className="self-end" size="sm" type="submit">
            Save
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
