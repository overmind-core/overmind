import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { DatasetPatch } from "@/hooks/use-datasets";
import type { Dataset } from "@/openapi";

export function RenameDataset({
  dataset,
  disabled,
  onSave,
}: {
  dataset: Dataset;
  disabled: boolean;
  onSave: (patch: DatasetPatch) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  const reset = (next: boolean) => {
    if (next) setName(dataset.name ?? "");
    setOpen(next);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed && trimmed !== (dataset.name ?? "")) onSave({ name: trimmed });
    setOpen(false);
  };

  return (
    <Popover onOpenChange={reset} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label="Rename dataset"
          disabled={disabled}
          size="icon-sm"
          title="Rename dataset"
          variant="ghost"
        >
          <Icon.edit />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-3">
        <form className="flex flex-col gap-3" onSubmit={submit}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dataset-name">Name</Label>
            <Input
              id="dataset-name"
              maxLength={255}
              onChange={(event) => setName(event.target.value)}
              value={name}
            />
          </div>
          <Button className="self-end" size="sm" type="submit">
            Save
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
