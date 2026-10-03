import { createContext, useContext } from "react";

export const WorkshopSidebarContext = createContext<{
  open: boolean;
  close: () => void;
  toggle: (trigger: HTMLButtonElement) => void;
} | null>(null);

export function useWorkshopSidebar() {
  const context = useContext(WorkshopSidebarContext);
  if (!context) throw new Error("Workshop sidebar requires the app layout.");
  return context;
}
