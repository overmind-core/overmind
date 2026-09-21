import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Icon } from "@/components/ui/icons";
import { useGuestGate } from "@/hooks/use-guest-gate";

export type DatasetSource = "file" | "traces";

export function NewDatasetButton({ onSelect }: { onSelect: (source: DatasetSource) => void }) {
  const guard = useGuestGate();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button>
          <Icon.datasetAdd />
          New dataset
          <Icon.chevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onSelect={guard(() => onSelect("file"))}>
          <Icon.upload /> Upload file
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={guard(() => onSelect("traces"))}>
          <Icon.observability /> Data from traces
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
