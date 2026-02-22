import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface SensitiveEntitiesChartProps {
  sensitiveEntities?: Record<string, number> | null;
}

export function SensitiveEntitiesChart({ sensitiveEntities }: SensitiveEntitiesChartProps) {
  const entries = Object.entries(sensitiveEntities ?? {});
  const maxCount = Math.max(0, ...entries.map(([, count]) => count), 1);

  if (entries.length === 0) {
    return (
      <div className="flex h-[140px] items-center justify-center py-6 text-center">
        <p className="text-sm text-muted-foreground">No sensitive entities detected</p>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div className="relative flex h-[140px] flex-col overflow-hidden px-1">
        <div className="mb-1 flex justify-between px-2">
          <span className="text-xs font-medium text-muted-foreground">0</span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil(maxCount / 4)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil(maxCount / 2)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil((maxCount * 3) / 4)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">{maxCount}</span>
        </div>

        <div className="relative flex flex-1 flex-col justify-around gap-2 pl-2 pr-1">
          <div className="absolute inset-y-0 left-4 right-2 z-0">
            {Array.from({ length: 5 }, (_, i) => (
              <div
                className="absolute top-0 bottom-0 w-px border-l-2 border-dashed border-muted-foreground/60"
                key={i}
                style={{ left: `${i * 25}%` }}
              />
            ))}
          </div>

          {entries.map(([type, count], index) => {
            const barWidth = (count / maxCount) * 100;
            const label = type.replace(/_/g, " ");
            return (
              <div className="relative z-10 flex h-4 w-full items-center" key={index}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div
                      className="flex h-5 items-center justify-start overflow-hidden rounded bg-primary px-1 transition-all hover:bg-primary/90"
                      style={{
                        minWidth: barWidth > 0 ? 60 : 0,
                        width: `${Math.max(barWidth, 2)}%`,
                      }}
                    >
                      <span className="truncate text-[10px] font-medium text-primary-foreground">
                        {label}
                      </span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>
                      {label}: {count}
                    </p>
                  </TooltipContent>
                </Tooltip>
              </div>
            );
          })}
        </div>
      </div>
    </TooltipProvider>
  );
}
