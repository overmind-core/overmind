import { useId, useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SearchInput } from "@/components/ui/search-input";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinetuningJobQuery } from "@/hooks/use-finetuning";
import type { FinetuningJobList } from "@/openapi";

type LabelSet = {
  question: string;
  kind: string;
  options: string[];
  option_values: number[] | null;
};

function DatasetLabels({
  job,
  showDataset,
  datasetName,
}: {
  job: FinetuningJobList;
  showDataset: boolean;
  datasetName?: string;
}) {
  const [search, setSearch] = useState("");
  const query = useFinetuningJobQuery(job.id);
  const summary = query.data?.record?.prediction_labels;
  const groups: LabelSet[] = summary?.label_sets ?? [];
  const matches = (option: string, question: string) =>
    `${question} ${option}`.toLowerCase().includes(search.trim().toLowerCase());
  return (
    <div className="flex min-w-0 flex-col items-end gap-1">
      {showDataset && (
        <EntityRef id={job.dataset} kind="dataset" name={datasetName} projectId={job.project} />
      )}
      {query.isPending ? (
        <Skeleton className="h-5 w-20 max-w-full" />
      ) : query.error || summary?.state !== "available" ? (
        <p className="text-muted-foreground">Unavailable</p>
      ) : groups.length === 0 ? (
        <p className="text-muted-foreground">Not recorded</p>
      ) : (
        <Popover onOpenChange={() => setSearch("")}>
          <PopoverTrigger asChild>
            <Button size="xs" variant="secondary">
              {summary.total_sets > 1
                ? `${summary.total_sets} label sets`
                : `${groups[0].options.length} labels`}
              <Icon.chevronDown aria-hidden />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            aria-label="Prediction labels"
            className="flex max-h-[min(24rem,var(--radix-popover-content-available-height))] w-96 max-w-[calc(100vw-2rem)] flex-col overflow-hidden text-xs"
          >
            <div className="shrink-0 border-b border-border/70 p-2">
              <SearchInput
                label="Search prediction labels"
                onChange={(event) => setSearch(event.target.value)}
                onClear={() => setSearch("")}
                placeholder="Search labels…"
                size="sm"
                value={search}
              />
            </div>
            <div className="min-h-0 overflow-y-auto overscroll-contain p-3">
              {groups.map((group, index) =>
                group.options.some((option) => matches(option, group.question)) ? (
                  <div className="mb-3 last:mb-0" key={index}>
                    <p className="mb-2 text-muted-foreground [overflow-wrap:anywhere]">
                      {group.question}
                    </p>
                    <ul aria-label={group.question} className="flex flex-wrap gap-1">
                      {group.options.map((option, optionIndex) =>
                        matches(option, group.question) ? (
                          <li className="min-w-0 max-w-full" key={option}>
                            <Badge
                              className="h-auto min-h-5 max-w-full bg-control px-1.5 whitespace-normal py-0.5 leading-4 [overflow-wrap:anywhere]"
                              size="chip"
                              variant="secondary"
                            >
                              <span className="min-w-0">{option}</span>
                              {group.option_values && (
                                <span className="ml-1.5 shrink-0 border-l border-border pl-1.5 tabular-nums text-muted-foreground">
                                  {group.option_values[optionIndex]}
                                </span>
                              )}
                            </Badge>
                          </li>
                        ) : null
                      )}
                    </ul>
                  </div>
                ) : null
              )}
              {!groups.some((group) =>
                group.options.some((option) => matches(option, group.question))
              ) && <p className="text-muted-foreground">No matching labels</p>}
            </div>
            {summary.total_sets > groups.length && (
              <p className="shrink-0 border-t border-border/70 px-3 py-2 text-muted-foreground">
                Showing {groups.length} of {summary.total_sets} label sets
              </p>
            )}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}

export function PredictionLabels({
  jobs,
  datasetNames,
}: {
  jobs: FinetuningJobList[];
  datasetNames?: Map<string, string>;
}) {
  const heading = useId();
  const datasets = [...new Map(jobs.map((job) => [job.cell ?? job.id, job])).values()];
  if (!datasets.length) return null;
  return (
    <div aria-labelledby={heading} className="flex max-w-full items-baseline gap-2" role="group">
      <dt className="shrink-0 text-muted-foreground" id={heading}>
        Prediction labels
      </dt>
      <dd className="flex min-w-0 max-w-[240px] flex-col gap-2">
        {datasets.map((job) => (
          <DatasetLabels
            datasetName={datasetNames?.get(job.dataset)}
            job={job}
            key={job.cell ?? job.id}
            showDataset={datasets.length > 1}
          />
        ))}
      </dd>
    </div>
  );
}
