import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useSetActiveModel } from "@/hooks/use-capability-active-model";
import { useDeployedModelQuery } from "@/hooks/use-inference";
import type { Capability, DeployedModel } from "@/openapi";

const STEPS = [
  { key: "checking", label: "Check deployment" },
  { key: "verifying", label: "Wake & verify" },
  { key: "switching", label: "Switch routing" },
];

export function activationPending(capability: Capability | undefined): boolean {
  return STEPS.some((step) => step.key === capability?.activation?.stage);
}

export function ModelActivationStatus({
  capability,
  models,
  showTraffic = true,
}: {
  capability: Capability;
  models: DeployedModel[];
  showTraffic?: boolean;
}) {
  const activation = capability.activation;
  const targetQuery = useDeployedModelQuery(activation?.target ?? undefined);
  const currentQuery = useDeployedModelQuery(capability.activeModel ?? undefined);
  const current = models.find((m) => m.id === capability.activeModel) ?? currentQuery.data ?? null;
  const target = models.find((m) => m.id === activation?.target) ?? targetQuery.data;
  const { setLive, isPending, narrowingConfirm } = useSetActiveModel(capability.id);
  const busy = activationPending(capability);
  const failed = activation?.stage === "failed";
  const stage = failed ? activation.failedStage : activation?.stage;
  const index = STEPS.findIndex((step) => step.key === stage);

  return (
    <div className="w-full min-w-0 space-y-2 text-xs">
      {busy || failed ? (
        <div className="space-y-2" role="status">
          <Progress label="Model activation" percent={((index + 1) / STEPS.length) * 100} />
          <p className="text-muted-foreground">
            Stage {index + 1}/{STEPS.length}: {STEPS[index]?.label}
            {failed ? " · Failed" : ""}
          </p>
          {failed ? <p className="text-destructive">{activation.error}</p> : null}
          {failed && target?.status === "ready" ? (
            <Button
              disabled={isPending}
              onClick={() => setLive(target, current)}
              size="sm"
              variant="secondary"
            >
              Retry activation
            </Button>
          ) : null}
        </div>
      ) : null}
      {showTraffic && capability.activeModel && !busy ? (
        <p
          className="text-muted-foreground"
          title={`Successful application requests to overmind/${capability.id}. Version-pinned requests are counted in model metrics.`}
        >
          {capability.firstApplicationRequestAt
            ? `First application request: ${capability.firstApplicationRequestAt.toLocaleString()}`
            : "Waiting for first application request"}
        </p>
      ) : null}
      {narrowingConfirm}
    </div>
  );
}
