import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from "react";

import { buildSpanTree } from "../lib/tree-building";
import { useTraceData } from "./TraceDataContext";

interface TraceSelectionContextValue {
  selectedSpanId: string | null;
  setSelectedSpanId: (id: string | null) => void;
}

const TraceSelectionContext = createContext<TraceSelectionContextValue | null>(null);

export function useTraceSelection(): TraceSelectionContextValue {
  const context = useContext(TraceSelectionContext);
  if (!context) {
    throw new Error("useTraceSelection must be used within a TraceSelectionProvider");
  }
  return context;
}

interface TraceSelectionProviderProps {
  children: ReactNode;
}

export function TraceSelectionProvider({ children }: TraceSelectionProviderProps) {
  const { traceId, spans } = useTraceData();
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);

  // Auto-select the root span once per trace, not whenever nothing is selected,
  // so a deselect sticks. The provider also stays mounted across traces.
  const autoSelectedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!traceId || spans.length === 0) return;
    if (autoSelectedFor.current === traceId) return;
    autoSelectedFor.current = traceId;
    // buildSpanTree, not the raw list, so "the root" is the same span the
    // step list shows first.
    setSelectedSpanId(buildSpanTree(spans)[0]?.id ?? null);
  }, [traceId, spans]);

  return (
    <TraceSelectionContext.Provider value={{ selectedSpanId, setSelectedSpanId }}>
      {children}
    </TraceSelectionContext.Provider>
  );
}
