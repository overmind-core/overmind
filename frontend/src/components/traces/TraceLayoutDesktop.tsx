import { createContext, type ReactNode, useContext, useRef, useState } from "react";
import {
  type ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";

const RESIZE_GROUP_ID = "trace-layout";
const HANDLE_ID = "trace-layout-handle";
const NAV_PANEL_ID = "trace-layout-nav";
const DETAIL_PANEL_ID = "trace-layout-detail";

const NAV_DEFAULT_PCT = 25;
const NAV_MIN_PCT = 20;
const NAV_COLLAPSED_PCT = 5;

interface TraceLayoutDesktopContextValue {
  isNavCollapsed: boolean;
  setIsNavCollapsed: (v: boolean) => void;
  panelRef: React.RefObject<ImperativePanelHandle | null>;
  handleTogglePanel: () => void;
}

const LayoutContext = createContext<TraceLayoutDesktopContextValue | null>(null);

function useLayoutContext() {
  const ctx = useContext(LayoutContext);
  if (!ctx) throw new Error("TraceLayoutDesktop components must be used within TraceLayoutDesktop");
  return ctx;
}

export function useTraceLayoutContext() {
  const ctx = useLayoutContext();
  return {
    handleTogglePanel: ctx.handleTogglePanel,
    isNavCollapsed: ctx.isNavCollapsed,
  };
}

export function TraceLayoutDesktop({ children }: { children: ReactNode }) {
  const [isNavCollapsed, setIsNavCollapsed] = useState(false);
  const [lastNavSize, setLastNavSize] = useState<number | null>(null);
  const panelRef = useRef<ImperativePanelHandle>(null);

  const handleTogglePanel = () => {
    if (!panelRef.current) return;
    if (isNavCollapsed) {
      panelRef.current.resize(lastNavSize ?? NAV_DEFAULT_PCT);
      setIsNavCollapsed(false);
    } else {
      const size = panelRef.current.getSize();
      setLastNavSize(size);
      panelRef.current.resize(NAV_COLLAPSED_PCT);
      setIsNavCollapsed(true);
    }
  };

  const ctx: TraceLayoutDesktopContextValue = {
    handleTogglePanel,
    isNavCollapsed,
    panelRef,
    setIsNavCollapsed,
  };

  return (
    <LayoutContext.Provider value={ctx}>
      <div className="h-full w-full">
        <PanelGroup direction="horizontal" id={RESIZE_GROUP_ID}>
          {children}
        </PanelGroup>
      </div>
    </LayoutContext.Provider>
  );
}

TraceLayoutDesktop.NavigationPanel = function NavPanel({ children }: { children: ReactNode }) {
  const { panelRef, setIsNavCollapsed } = useLayoutContext();
  return (
    <Panel
      collapsedSize={NAV_COLLAPSED_PCT}
      collapsible={false}
      defaultSize={NAV_DEFAULT_PCT}
      id={NAV_PANEL_ID}
      minSize={NAV_MIN_PCT}
      onCollapse={() => setIsNavCollapsed(true)}
      onExpand={() => setIsNavCollapsed(false)}
      ref={panelRef}
    >
      {children}
    </Panel>
  );
};

TraceLayoutDesktop.ResizeHandle = function ResizeHandle() {
  const { handleTogglePanel } = useLayoutContext();
  return (
    <PanelResizeHandle
      className="relative w-px bg-border transition-colors after:absolute after:inset-y-0 after:left-0 after:w-1 after:bg-primary/20 after:opacity-0 hover:after:opacity-100 data-[resize-handle-state='drag']:after:opacity-100"
      id={HANDLE_ID}
      onDoubleClick={handleTogglePanel}
    />
  );
};

TraceLayoutDesktop.DetailPanel = function DetailPanel({ children }: { children: ReactNode }) {
  return (
    <Panel defaultSize={65} id={DETAIL_PANEL_ID} minSize={40}>
      {children}
    </Panel>
  );
};
