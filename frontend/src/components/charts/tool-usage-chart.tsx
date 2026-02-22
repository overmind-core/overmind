import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

interface ToolData {
  tool_name: string;
  total_calls: number;
}

interface ToolUsageChartProps {
  loading?: boolean;
  toolData?: ToolData[] | null;
}

export function ToolUsageChart({ loading, toolData }: ToolUsageChartProps) {
  if (loading) {
    return <Skeleton className="h-[140px] w-full rounded-md" />;
  }

  if (!toolData || !Array.isArray(toolData) || toolData.length === 0) {
    return (
      <div className="flex h-[140px] items-center justify-center py-6 text-center">
        <p className="text-sm text-muted-foreground">No tool usage data available</p>
      </div>
    );
  }

  const maxCalls = Math.max(...toolData.map((t) => t.total_calls), 1);

  return (
    <TooltipProvider>
      <div className="relative flex h-[140px] flex-col overflow-hidden px-1">
        <div className="mb-1 flex justify-between px-2">
          <span className="text-xs font-medium text-muted-foreground">0</span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil(maxCalls / 4)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil(maxCalls / 2)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">
            {Math.ceil((maxCalls * 3) / 4)}
          </span>
          <span className="text-xs font-medium text-muted-foreground">{maxCalls}</span>
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

          {toolData.map((tool, index) => {
            const barWidth = (tool.total_calls / maxCalls) * 100;
            return (
              <div className="relative z-10 flex h-[18px] w-full items-center" key={index}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div
                      className="flex h-6 items-center justify-start overflow-hidden rounded bg-primary px-1 transition-all hover:bg-primary/90"
                      style={{
                        minWidth: barWidth > 0 ? 60 : 0,
                        width: `${Math.max(barWidth, 2)}%`,
                      }}
                    >
                      <span className="truncate text-[10px] font-medium text-primary-foreground">
                        {tool.tool_name}
                      </span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>
                      {tool.tool_name}: {tool.total_calls}
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
