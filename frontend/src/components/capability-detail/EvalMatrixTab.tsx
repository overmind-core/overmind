import { useState } from "react";

import { EvalMatrixTable } from "@/components/evaluations/eval-matrix-table";
import { EvalSetDetailDialog } from "@/components/evaluations/eval-set-detail-dialog";
import { RunLocallyDialog } from "@/components/optimiser/run-locally-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { useEvalSetsQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { getEvalPreload } from "@/lib/eval-preload";
import type { Capability, EvalSet } from "@/openapi";

export function EvalMatrixTab({
  capability,
  capabilityId,
  projectId,
}: {
  capability: Capability;
  capabilityId: string;
  projectId: string;
}) {
  const setsQuery = useEvalSetsQuery(projectId);
  const guard = useGuestGate();
  const [optimiseOpen, setOptimiseOpen] = useState(false);
  const [viewing, setViewing] = useState<EvalSet | null>(null);
  const sets = (setsQuery.data?.results ?? []).filter((s) => s.capability === capabilityId);
  const active = sets.find((s) => s.isActive) ?? null;
  const counts = getEvalPreload(capability)?.counts;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {active && (
          <Button onClick={() => setViewing(active)} size="sm" variant="secondary">
            {active.name} · {active.rows?.length ?? 0} rows
          </Button>
        )}
        {counts && (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Badge size="chip" variant="neutral">
              {counts.expectations ?? 0} expectations
            </Badge>
            <Badge size="chip" variant="neutral">
              {counts.checks ?? 0} checks
            </Badge>
          </span>
        )}
        <Button
          className="ml-auto"
          onClick={guard(() => setOptimiseOpen(true))}
          size="sm"
          variant="secondary"
        >
          <Icon.optimiser />
          Optimise
        </Button>
      </div>
      <EvalMatrixTable capabilityId={capabilityId} projectId={projectId} />
      <EvalSetDetailDialog onClose={() => setViewing(null)} set={viewing} />
      <RunLocallyDialog
        capabilityId={capabilityId}
        onOpenChange={setOptimiseOpen}
        open={optimiseOpen}
        projectId={projectId}
      />
    </div>
  );
}
