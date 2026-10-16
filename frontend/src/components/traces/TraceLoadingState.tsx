import { Skeleton } from "@/components/ui/skeleton";

export function TraceLoadingState() {
  return (
    <div
      aria-busy="true"
      aria-label="Loading trace"
      className="h-full overflow-hidden"
      role="status"
    >
      <span className="sr-only">Loading trace</span>
      <div aria-hidden="true" className="flex h-full flex-col md:flex-row">
        <div className="h-[30%] min-h-36 shrink-0 overflow-hidden border-b border-border md:h-full md:w-[35%] md:border-r md:border-b-0">
          <div className="flex h-10 items-center gap-3 border-b border-border/70 px-3">
            <Skeleton className="h-4 w-12" />
            <Skeleton className="h-4 w-16" />
          </div>
          <div className="border-b border-border/70 p-2">
            <Skeleton className="h-7 w-full" />
          </div>
          <div className="space-y-4 p-3">
            {[0, 1, 2, 2, 1, 2].map((depth, index) => (
              <div
                className="flex gap-2"
                key={`${depth}-${index}`}
                style={{ paddingLeft: depth * 16 }}
              >
                <Skeleton className="size-3 shrink-0" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3 w-4/5" />
                  <Skeleton className="h-2 w-2/5" />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="space-y-3 border-b border-border/70 p-3">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-1/3" />
            <div className="flex gap-4 pt-2">
              <Skeleton className="h-3 w-14" />
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-12" />
            </div>
          </div>
          <div className="space-y-8 p-3">
            {["input", "output"].map((section) => (
              <div className="space-y-3" key={section}>
                <Skeleton className="h-3 w-12" />
                <div className="space-y-2">
                  <Skeleton className="h-3 w-5/6" />
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-2/3" />
                  <Skeleton className="h-3 w-3/4" />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
