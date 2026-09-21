import { EVALUATOR_KIND_LABEL } from "@/components/evaluations/evaluator-kind";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { EvalSet } from "@/openapi";

export function EvalSetDetailDialog({
  set,
  onClose,
}: {
  set: EvalSet | null;
  onClose: () => void;
}) {
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
            <DialogDescription>{set?.members.length ?? 0} members</DialogDescription>
          </div>
        </DialogHeader>
        <DialogBody>
          <ul aria-label="Eval set members" className="space-y-2">
            {set?.members.map((member) => (
              <li
                className="flex flex-wrap items-center gap-3 rounded-sm border border-border bg-muted px-3 py-2 text-sm"
                key={member.id}
              >
                <span className="min-w-0 flex-1 truncate">
                  {member.evaluatorDisplayName || member.evaluatorName}
                </span>
                <Badge size="chip" variant="neutral">
                  {EVALUATOR_KIND_LABEL[member.evaluatorKind] ?? member.evaluatorKind}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {member.role === "generative" ? "Generative" : "Trace scoring"}
                </span>
                {member.evaluatorCapabilityName && (
                  <span className="text-xs text-muted-foreground">
                    {member.evaluatorCapabilityName}
                  </span>
                )}
                {!member.enabled && (
                  <Badge size="chip" variant="neutral">
                    Disabled
                  </Badge>
                )}
              </li>
            ))}
          </ul>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
