import { useState } from "react";

import { EvalMatrixTable } from "@/components/evaluations/eval-matrix-table";
import { RunLocallyDialog } from "@/components/optimiser/run-locally-dialog";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { useGuestGate } from "@/hooks/use-guest-gate";
import type { Capability } from "@/openapi";

export function EvalMatrixTab({
  capabilityId,
  capability,
  projectId,
}: {
  capabilityId: string;
  capability: Capability;
  projectId: string;
}) {
  const guard = useGuestGate();
  const [optimiseOpen, setOptimiseOpen] = useState(false);

  return (
    <>
      <EvalMatrixTable
        actions={
          <Button onClick={guard(() => setOptimiseOpen(true))} size="sm" variant="secondary">
            <Icon.optimiser />
            Optimise
          </Button>
        }
        capability={capability}
        capabilityId={capabilityId}
        projectId={projectId}
      />
      <RunLocallyDialog
        capabilityId={capabilityId}
        onOpenChange={setOptimiseOpen}
        open={optimiseOpen}
        projectId={projectId}
      />
    </>
  );
}
