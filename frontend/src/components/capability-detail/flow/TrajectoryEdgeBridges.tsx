import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";

import { type RenderedTrajectoryEdge, renderedCrossings } from "./trajectoryRenderedCrossings";

const BridgeContext = createContext<{
  paths: ReturnType<typeof renderedCrossings>;
  register: (edge: RenderedTrajectoryEdge) => void;
  remove: (id: string) => void;
} | null>(null);

export function TrajectoryEdgeBridges({ children }: { children: ReactNode }) {
  const [edges, setEdges] = useState(new Map<string, RenderedTrajectoryEdge>());
  const register = useCallback((edge: RenderedTrajectoryEdge) => {
    setEdges((current) => {
      const old = current.get(edge.id);
      if (old?.path === edge.path && old.source === edge.source && old.target === edge.target)
        return current;
      return new Map(current).set(edge.id, edge);
    });
  }, []);
  const remove = useCallback((id: string) => {
    setEdges((current) => {
      if (!current.has(id)) return current;
      const next = new Map(current);
      next.delete(id);
      return next;
    });
  }, []);
  const paths = useMemo(() => renderedCrossings([...edges.values()]), [edges]);
  const value = useMemo(() => ({ paths, register, remove }), [paths, register, remove]);
  return <BridgeContext.Provider value={value}>{children}</BridgeContext.Provider>;
}

export function useTrajectoryBridges(edge: RenderedTrajectoryEdge) {
  const context = useContext(BridgeContext);
  const register = context?.register,
    remove = context?.remove;
  const { id, source, target, path } = edge;
  useLayoutEffect(() => {
    register?.({ id, path, source, target });
  }, [register, id, source, target, path]);
  useLayoutEffect(() => () => remove?.(id), [remove, id]);
  const current = context?.paths.get(id);
  return current?.original === path ? current : { bridges: [], path };
}
