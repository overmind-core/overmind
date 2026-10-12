import { createContext, useContext } from "react";

export interface WorkshopSidebar {
  open: boolean;
  close: () => void;
  /** The trigger gets focus back when the sidebar closes. */
  toggle: (trigger: HTMLElement | null) => void;
}

export const WorkshopSidebarContext = createContext<WorkshopSidebar | null>(null);

export function useWorkshopSidebar(): WorkshopSidebar {
  const sidebar = useContext(WorkshopSidebarContext);
  if (!sidebar) throw new Error("The workshop sidebar needs the app layout.");
  return sidebar;
}
