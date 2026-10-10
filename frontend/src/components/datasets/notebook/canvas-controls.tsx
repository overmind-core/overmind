import { WorkshopSwitcher } from "@/components/datasets/workshop-sidebar";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";

export function NotebookControls({
  showMinimap,
  onToggleMinimap,
}: {
  showMinimap: boolean;
  onToggleMinimap?: () => void;
}) {
  return (
    <nav
      aria-label="Workshop controls"
      className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-10 flex-col items-center pt-3"
    >
      <div className="pointer-events-auto flex flex-col items-center rounded-sm border border-border bg-card py-1">
        <WorkshopSwitcher iconOnly />
        {onToggleMinimap && (
          <Button
            aria-label={showMinimap ? "Hide minimap" : "Show minimap"}
            aria-pressed={showMinimap}
            className={showMinimap ? "bg-accent" : undefined}
            onClick={onToggleMinimap}
            size="icon-sm"
            title={showMinimap ? "Hide minimap" : "Show minimap"}
            variant="ghost"
          >
            <Icon.minimap />
          </Button>
        )}
      </div>
    </nav>
  );
}
