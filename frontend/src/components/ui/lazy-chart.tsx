import { type ComponentProps, type ComponentType, lazy, type ReactElement, Suspense } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { withChunkReloadRetry } from "@/lib/chunk-error";

/** React.lazy + Suspense with a Skeleton fallback, keeping a heavy chart library
 *  (recharts, @xyflow/react) out of the initial bundle. */
export function lazyChart<T extends ComponentType<any>>(
  loader: () => Promise<{ default: T }>,
  { minHeight = 200 }: { minHeight?: number } = {}
): (props: ComponentProps<T>) => ReactElement {
  const Lazy = lazy(withChunkReloadRetry(loader));
  return function LazyChart(props: ComponentProps<T>) {
    return (
      <Suspense fallback={<Skeleton className="w-full rounded-md" style={{ minHeight }} />}>
        <Lazy {...props} />
      </Suspense>
    );
  };
}
