import { formatScore, TIER_ORDER } from "@/components/finetuning/train/model-config";
import { ModelOptionLabel } from "@/components/model-option-label";
import { SelectItem } from "@/components/ui/select";
import type { ModelCatalog, ModelEntry } from "@/hooks/use-finetuning";
import type { FinetuningExperiment } from "@/openapi";

/** The ranked candidates keyed by catalog id, so a picker row can show its match. */
export type GradeIndex = ReadonlyMap<string, FinetuningExperiment>;

export interface RankedModel {
  tier: string;
  model: ModelEntry;
  match: number | null;
}

/** The API includes `disabled` entries so other consumers can resolve them by
    id; no picker surface may offer one for selection. */
function catalogEntries(catalog: ModelCatalog, exclude?: Set<string>): RankedModel[] {
  const tiers = catalog.tiers.length ? catalog.tiers : [...TIER_ORDER];
  return tiers.flatMap((tier) =>
    ((catalog.models[tier] ?? []) as ModelEntry[])
      .filter((m) => !m.disabled && !exclude?.has(m.id))
      .map((model) => ({ match: null, model, tier }))
  );
}

export function findCatalogModel(
  catalog: ModelCatalog,
  modelId: string
): { tier: string; model: ModelEntry } | null {
  const found = catalogEntries(catalog).find((entry) => entry.model.id === modelId);
  return found ? { model: found.model, tier: found.tier } : null;
}

/** Match descending. Ungraded models sort last, smallest first — a model with no
 *  benchmark data is never ordered as if it scored zero. */
export function rankedModels(
  catalog: ModelCatalog,
  grades?: GradeIndex,
  exclude?: Set<string>
): RankedModel[] {
  return catalogEntries(catalog, exclude)
    .map((entry) => ({ ...entry, match: grades?.get(entry.model.id)?.match ?? null }))
    .sort(
      (a, b) =>
        Number(a.match == null) - Number(b.match == null) ||
        (b.match ?? 0) - (a.match ?? 0) ||
        (a.model.totalParamsB ?? 0) - (b.model.totalParamsB ?? 0) ||
        a.model.display.localeCompare(b.model.display)
    );
}

function ModelOptionRow({ entry }: { entry: RankedModel }) {
  return (
    <ModelOptionLabel model={entry.model.id} name={entry.model.display}>
      <span className="ml-auto shrink-0 text-xs text-muted-foreground">{entry.model.params}</span>
      <span
        className="w-6 shrink-0 text-right text-xs tabular-nums text-foreground"
        title={entry.match == null ? "Not graded" : `Match ${formatScore(entry.match)}`}
      >
        {formatScore(entry.match)}
      </span>
    </ModelOptionLabel>
  );
}

export function ModelSelectOptions({
  catalog,
  grades,
}: {
  catalog: ModelCatalog;
  grades?: GradeIndex;
}) {
  return (
    <>
      {rankedModels(catalog, grades).map((entry) => (
        <SelectItem key={entry.model.id} textValue={entry.model.display} value={entry.model.id}>
          <ModelOptionRow entry={entry} />
        </SelectItem>
      ))}
    </>
  );
}

/** Buttons in a Popover, not a Select: a value-less Radix Select never opens —
    its modal portal aria-hides the app root around the focused trigger. */
export function ModelPickerList({
  catalog,
  exclude,
  grades,
  onPick,
}: {
  catalog: ModelCatalog;
  exclude?: Set<string>;
  grades?: GradeIndex;
  onPick: (modelId: string) => void;
}) {
  const models = rankedModels(catalog, grades, exclude);
  if (models.length === 0) {
    return <p className="px-2 py-3 text-xs text-muted-foreground">No models left to add.</p>;
  }
  return (
    <div className="flex max-h-[19rem] flex-col">
      <p className="pixel-label shrink-0 border-b border-border/70 px-3 py-2 text-xs text-muted-foreground">
        Ranked by match
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto p-1">
        {models.map((entry) => (
          <button
            className="flex w-full items-center rounded-sm px-2 py-1.5 text-left text-sm hover:bg-wash-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            key={entry.model.id}
            onClick={() => onPick(entry.model.id)}
            type="button"
          >
            <ModelOptionRow entry={entry} />
          </button>
        ))}
      </div>
    </div>
  );
}
