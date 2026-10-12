import { createContext, useContext } from "react";

export interface WorkshopSidebar {
  open: boolean;
  close: () => void;
  toggle: (trigger: HTMLElement | null) => void;
}

export const WorkshopSidebarContext = createContext<WorkshopSidebar | null>(null);

export function useWorkshopSidebar(): WorkshopSidebar {
  const sidebar = useContext(WorkshopSidebarContext);
  if (!sidebar) throw new Error("The workshop sidebar needs the app layout.");
  return sidebar;
}
