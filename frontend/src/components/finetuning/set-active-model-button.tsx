import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { blockedReason, useSetActiveModel } from "@/hooks/use-capability-active-model";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { DeployedModel } from "@/openapi";

const NO_PR_EXPLAINER = "Takes effect on the next request to this capability's alias.";

const UNROUTED_EXPLAINER = "This capability's alias has no model behind it, so calls to it fail.";

const NO_DEPLOYMENT_EXPLAINER =
  "This run's deployment is no longer available, so there is nothing to make live.";

/** Guards come from the shared mutation: ready-only, project match (server-side
    `validate_active_model`), and the context-narrowing confirmation. */
export function SetActiveModelButton({
  capabilityId,
  candidate,
  incumbent,
  promote = false,
}: {
  capabilityId: string;
  /** Absent when the deployment was deleted or sits outside the page of models
      the mount reads — that is the button's blocked face, not a missing button. */
  candidate?: DeployedModel;
  /** What the alias answers with today; null when it answers nothing. */
  incumbent: DeployedModel | null;
  promote?: boolean;
}) {
  const { isPending, narrowingConfirm, setLive } = useSetActiveModel(capabilityId);
  const reason = candidate ? blockedReason(candidate.status) : NO_DEPLOYMENT_EXPLAINER;
  const inert = !!reason || isPending;

  const handleClick = () => {
    if (inert || !candidate) return;
    setLive(candidate, incumbent);
  };

  const button = (
    <Button
      aria-busy={isPending || undefined}
      aria-disabled={inert || undefined}
      // Names the action and candidate; a blocked reason belongs in the tooltip.
      aria-label={`Make live — ${candidate?.modelId ?? "deployment unavailable"}`}
      className={cn(reason && "opacity-50")}
      onClick={handleClick}
      size="sm"
      type="button"
      // A blocked control never takes the primary face: an ink fill at 50%
      // opacity lands ~3.2:1 against its own label.
      variant={promote && !reason ? "default" : "secondary"}
    >
      {isPending ? <Spinner className="text-current" /> : <Icon.server />}
      {isPending ? "Switching…" : "Make live"}
    </Button>
  );

  return (
    <>
      <Tooltip>
        {/* `aria-disabled` + a gated handler, never `disabled`: a real
            `disabled` cancels pointer events, so this tooltip could not open. */}
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent className="max-w-[18rem]" side="top">
          <p className={cn(PROSE, "text-xs leading-snug")}>
            {reason ?? (incumbent ? NO_PR_EXPLAINER : UNROUTED_EXPLAINER)}
          </p>
        </TooltipContent>
      </Tooltip>

      {narrowingConfirm}
    </>
  );
}

/** A badge, not a disabled button: `disabled:opacity-50` puts the label under
 *  the contrast floor. `h-7` holds the 28px ramp beside `size="sm"` neighbours.
 *  "Live model", not "Live" — the row's `ServingStatusBadge` says "Live" for a
 *  GPU answering now, and the two legitimately disagree when scaled to zero. */
export function LiveModelBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* Focusable so the tooltip is reachable without a pointer. */}
        <Badge className="h-7 gap-1.5" tabIndex={0} variant="success">
          <span aria-hidden className="size-1.5 shrink-0 rounded-xs bg-success" />
          Live model
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-[18rem]" side="top">
        This model already answers the capability&apos;s alias.
      </TooltipContent>
    </Tooltip>
  );
}
