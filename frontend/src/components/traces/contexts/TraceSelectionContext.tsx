import { createContext, type ReactNode, useCallback, useContext, useState } from "react";

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
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  const [collapsedNodes, setCollapsedNodes] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<"tree" | "timeline">("tree");

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
