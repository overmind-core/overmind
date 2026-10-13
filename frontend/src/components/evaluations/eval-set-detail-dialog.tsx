import { MatrixRowLabel } from "@/components/evaluations/matrix-row-label";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useEvalMatrixQuery } from "@/hooks/use-evaluations";
import type { EvalSet } from "@/openapi";

export function EvalSetDetailDialog({
  set,
  onClose,
}: {
  set: EvalSet | null;
  onClose: () => void;
}) {
  const matrix = useEvalMatrixQuery(set?.project);
  const ids = new Set(set?.rows ?? []);
  const rows = (matrix.data ?? []).filter((row) => ids.has(row.id));
  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open={!!set}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <div>
            <DialogTitle>{set?.name}</DialogTitle>
            <DialogDescription>{ids.size} rows</DialogDescription>
          </div>
        </DialogHeader>
        <DialogBody>
          <ul aria-label="Eval set rows" className="space-y-2">
            {rows.map((row) => (
              <li
                className="flex flex-wrap items-center gap-3 rounded-sm border border-border bg-muted px-3 py-2 text-sm"
                key={row.id}
              >
                <MatrixRowLabel row={row} />
              </li>
            ))}
          </ul>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
