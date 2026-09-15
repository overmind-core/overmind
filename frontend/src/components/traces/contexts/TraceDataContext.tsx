import { createContext, type ReactNode, useContext } from "react";

import type { SpanRow, TraceStatus } from "@/hooks/use-traces";
import type { TraceUsage } from "@/openapi";

interface TraceDataContextValue {
  traceId: string;
  projectId: string;
  spans: SpanRow[];
  isLoading: boolean;
  traceStatus?: TraceStatus;
  usage?: TraceUsage;
}

const TraceDataContext = createContext<TraceDataContextValue | null>(null);

export function useTraceData(): TraceDataContextValue {
  const context = useContext(TraceDataContext);
  if (!context) {
    throw new Error("useTraceData must be used within a TraceDataProvider");
  }
  return context;
}

interface TraceDataProviderProps {
  children: ReactNode;
  value: TraceDataContextValue;
}

export function TraceDataProvider({ children, value }: TraceDataProviderProps) {
  return <TraceDataContext.Provider value={value}>{children}</TraceDataContext.Provider>;
}
