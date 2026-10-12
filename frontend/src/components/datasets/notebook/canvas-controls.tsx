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
  const label = showMinimap ? "Hide minimap" : "Show minimap";
  return (
    <nav aria-label="Workshop controls" className="flex items-center gap-1">
      <WorkshopSwitcher />
      {onToggleMinimap && (
        <Button
          aria-label={label}
          aria-pressed={showMinimap}
          onClick={onToggleMinimap}
          size="icon-sm"
          title={label}
          variant="ghost"
        >
          <Icon.minimap />
        </Button>
      )}
    </nav>
  );
}
