import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { buildSpanTree } from "../lib/tree-building";
import { useTraceData } from "./TraceDataContext";

interface TraceSelectionContextValue {
  selectedSpanId: string | null;
  setSelectedSpanId: (id: string | null) => void;
  collapsedNodes: Set<string>;
  toggleCollapsed: (id: string) => void;
  expandAll: () => void;
  collapseAll: (nodeIds: string[]) => void;
  viewMode: "tree" | "timeline";
  setViewMode: (mode: "tree" | "timeline") => void;
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
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<"tree" | "timeline">("tree");

  // Auto-select the root span once per trace, not whenever nothing is selected:
  // re-selecting on every null would make SpanTree's click-to-deselect impossible.
  // The provider also stays mounted across traces.
  const autoSelectedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!traceId || spans.length === 0) return;
    if (autoSelectedFor.current === traceId) return;
    autoSelectedFor.current = traceId;
    // buildSpanTree, not the raw list, so "the root" is the same span the
    // navigation tree shows first.
    setSelectedSpanId(buildSpanTree(spans)[0]?.id ?? null);
  }, [traceId, spans]);

  const toggleCollapsed = useCallback((id: string) => {
    setCollapsedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => setCollapsedNodes(new Set()), []);
  const collapseAll = useCallback((nodeIds: string[]) => {
    setCollapsedNodes(new Set(nodeIds));
  }, []);

  const value: TraceSelectionContextValue = {
    collapseAll,
    collapsedNodes,
    expandAll,
    selectedSpanId,
    setSelectedSpanId,
    setViewMode,
    toggleCollapsed,
    viewMode,
  };

  return <TraceSelectionContext.Provider value={value}>{children}</TraceSelectionContext.Provider>;
}
