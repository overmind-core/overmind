import { Badge } from "@/components/ui/badge";
import { domainStatus, TONE_CHIP } from "@/lib/colors";
import { cn } from "@/lib/utils";

export function DeployedModelStatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  return (
    <Badge
      className={cn(
        // shrink-0: in a capped row the model name must truncate, not this badge.
        "w-24 shrink-0 justify-center whitespace-nowrap border text-xs font-medium tracking-normal",
        TONE_CHIP[domainStatus(status)],
        className
      )}
      variant="outline"
    >
      {status}
    </Badge>
  );
}
