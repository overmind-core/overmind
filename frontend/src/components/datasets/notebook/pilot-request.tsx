import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Cell } from "@/openapi";

export function PilotRequest({
  cell,
  disabled,
  onRequest,
}: {
  cell: Pick<Cell, "id" | "version" | "rows" | "fingerprint">;
  disabled: boolean;
  onRequest: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(Math.min(50_000, Math.max(1, Math.floor(cell.rows / 10))));
  const valid = Number.isInteger(count) && count > 0 && count < cell.rows;
  return (
    <div className="mb-2 mr-2 text-xs">
      {!open ? (
        <Button
          disabled={disabled || cell.rows < 2}
          onClick={() => setOpen(true)}
          size="sm"
          variant="outline"
        >
          Create representative pilot
        </Button>
      ) : (
        <form
          className="flex flex-wrap items-end gap-3 rounded-md border border-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!valid || disabled) return;
            onRequest(
              `Prepare a representative pilot proposal of ${count} unchanged rows from cell ${cell.id}, version ${cell.version}, fingerprint ${cell.fingerprint}. Explore the source and save a preparation plan before selecting rows. Choose relevant source/family, length, option-count and target-type strata from the actual data; show parent and sample coverage and uncovered strata. Use deterministic sampling with a saved seed and exact lineage. Preserve repeated observations, option order, probability targets and weights. Present the concrete selection for review before activation. This is a pilot sample, not a train/eval split or proof of generality.`
            );
            setOpen(false);
          }}
        >
          <label className="flex flex-col gap-1">
            Pilot rows
            <Input
              aria-label="Pilot rows"
              className="w-36"
              max={cell.rows - 1}
              min={1}
              onChange={(event) => setCount(Number(event.target.value))}
              type="number"
              value={count}
            />
          </label>
          <Button disabled={!valid || disabled} size="sm" type="submit">
            Prepare pilot proposal
          </Button>
          <Button onClick={() => setOpen(false)} size="sm" type="button" variant="outline">
            Cancel
          </Button>
          <p className="w-full text-muted-foreground">
            Source {cell.version} · {cell.rows.toLocaleString()} rows. Coverage and row changes
            appear in the proposal.
          </p>
        </form>
      )}
    </div>
  );
}
