import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { SelectionBar } from "@/components/ui/selection-bar";

interface TraceSelectionBarProps {
  /** Page rows, or the all-pages count once `allPagesSelected`. */
  selectedCount: number;
  /** Traces matching the active filters across every page. */
  totalCount: number;
  allPagesSelected: boolean;
  onSelectAllPages: () => void;
  onClear: () => void;
  onAddToDataset?: () => void;
}

function SelectAllControl({
  allPagesSelected,
  totalCount,
  hasMoreAcrossPages,
  onSelectAllPages,
}: {
  allPagesSelected: boolean;
  totalCount: number;
  hasMoreAcrossPages: boolean;
  onSelectAllPages: () => void;
}) {
  if (allPagesSelected) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
        <Icon.success className="size-3.5" />
        All pages
      </span>
    );
  }

  if (!hasMoreAcrossPages) return null;

  return (
    <Button onClick={onSelectAllPages} size="sm" variant="secondary">
      Select all {totalCount.toLocaleString()}
    </Button>
  );
}

export function TraceSelectionBar({
  selectedCount,
  totalCount,
  allPagesSelected,
  onSelectAllPages,
  onClear,
  onAddToDataset,
}: TraceSelectionBarProps) {
  const hasMoreAcrossPages = !allPagesSelected && totalCount > selectedCount;

  return (
    <SelectionBar
      afterCount={
        <SelectAllControl
          allPagesSelected={allPagesSelected}
          hasMoreAcrossPages={hasMoreAcrossPages}
          onSelectAllPages={onSelectAllPages}
          totalCount={totalCount}
        />
      }
      anchor="viewport"
      count={selectedCount}
      countLabel={selectedCount === 1 ? "trace selected" : "traces selected"}
      onClear={onClear}
      regionLabel="Trace selection actions"
    >
      {onAddToDataset && (
        <Button onClick={onAddToDataset} size="sm">
          <Icon.datasetAdd />
          Add to dataset
        </Button>
      )}
    </SelectionBar>
  );
}
