import type { Cached } from "~/lib/api";

import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

const time = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  month: "short",
  timeZoneName: "short",
});

export function Freshness({ data }: { data?: Cached }) {
  if (!data) return null;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="flex items-center gap-1.5 rounded-sm px-1.5 py-1 text-xs text-muted-foreground hover:text-foreground">
          <Icon.history className="size-3.5" />
          {time.format(new Date(data.computed_at))}
        </TooltipTrigger>
        <TooltipContent>
          Computed {time.format(new Date(data.computed_at))}. Refreshes{" "}
          {time.format(new Date(data.refreshes_at))}.
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
