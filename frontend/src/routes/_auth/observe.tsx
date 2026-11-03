import { useCallback, useMemo, useState } from "react";

import { createFileRoute } from "@tanstack/react-router";

import { ActivityTable } from "@/components/observe/activity-table";
import { IssueSheet } from "@/components/observe/issue-sheet";
import { WhatToKnow } from "@/components/observe/signals-to-train";
import { TraceRun } from "@/components/observe/trace-run";
import { TrainDialog } from "@/components/observe/train-dialog";
import { TrendPanel } from "@/components/observe/trend-panel";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCapabilityActivity, useObservabilityOverview } from "@/hooks/use-issues";
import { formatNumber } from "@/lib/formatters";
import { type ObserveSearch, observeSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/observe")({
  component: ObservePage,
  validateSearch: observeSearchSchema,
});

const RANGE_WORDS: Record<ObserveSearch["range"], string> = {
  "7d": "7 days",
  "24h": "24 hours",
  "30d": "30 days",
  "90d": "90 days",
  "180d": "180 days",
};

function Controls({
  search,
  scope,
  onChange,
}: {
  search: ObserveSearch;
  scope: { projectId: string; range: string };
  onChange: (updates: Partial<ObserveSearch>) => void;
}) {
  const capabilities = useCapabilityActivity(scope, true);
  return (
    <div className="flex items-center gap-2">
      <Tabs
        onValueChange={(range) => onChange({ page: 1, range: range as ObserveSearch["range"] })}
        value={search.range}
      >
        <TabsList aria-label="Time range">
          <TabsTrigger value="24h">24h</TabsTrigger>
          <TabsTrigger value="7d">7d</TabsTrigger>
          <TabsTrigger value="30d">30d</TabsTrigger>
          <TabsTrigger value="90d">90d</TabsTrigger>
          <TabsTrigger value="180d">180d</TabsTrigger>
        </TabsList>
      </Tabs>
      <Select
        onValueChange={(value) =>
          onChange({ capability: value === "all" ? undefined : value, page: 1 })
        }
        value={search.capability ?? "all"}
      >
        <SelectTrigger aria-label="Capability" className="w-52">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All capabilities</SelectItem>
          {capabilities.data?.capabilities.map((item) => (
            <SelectItem key={item.capability} value={item.capability}>
              {item.capabilityName}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function ObservePage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { projectId, range, capability } = search;

  const onChange = useCallback(
    (updates: Partial<ObserveSearch>) =>
      navigate({ resetScroll: false, search: (prev) => ({ ...prev, ...updates }) }),
    [navigate]
  );

  const scope = useMemo(
    () => ({ capability, projectId: projectId ?? "", range }),
    [capability, projectId, range]
  );
  const overview = useObservabilityOverview(scope);
  const capabilities = useCapabilityActivity({ projectId: projectId ?? "", range }, !!projectId);
  const [training, setTraining] = useState<{
    capability: string;
    capabilityName: string;
  } | null>(null);

  const header = (
    <PageHeader
      actions={
        projectId && <Controls onChange={onChange} scope={{ projectId, range }} search={search} />
      }
      className="items-center"
      description={
        overview.data &&
        `${formatNumber(overview.data.tracesChecked)} traces checked in the last ${RANGE_WORDS[range]}.`
      }
      icon={
        <Icon.observabilityTitle
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] invert"
        />
      }
      title="Observability"
    />
  );

  if (!projectId) {
    return (
      <PageShell header={header} variant="scroll">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  const data = overview.data;

  return (
    <PageShell header={header} variant="scroll">
      <div className="flex flex-col gap-8 pb-10">
        {data ? (
          <WhatToKnow
            onOpen={(issue) => onChange({ issue })}
            onSeeIssues={(capability) =>
              onChange({ capability, cause: "model", group: "issue", page: 1 })
            }
            onSeeQueue={() => onChange({ group: "capability", page: 1 })}
            onSeeSystem={() => onChange({ cause: "system", group: "issue", page: 1 })}
            onTrain={setTraining}
            overview={data}
          />
        ) : (
          <Skeleton className="h-72 w-full rounded-md" />
        )}
        <TrendPanel
          metric={search.metric}
          onMetric={(metric) => onChange({ metric })}
          scope={scope}
        />
        <ActivityTable
          onChange={onChange}
          onOpenIssue={(issue) => onChange({ issue })}
          onOpenTrace={(trace) => onChange({ trace })}
          onTrain={setTraining}
          scope={scope}
          search={search}
        />
      </div>

      <IssueSheet
        issueId={search.trace ? undefined : search.issue}
        onClose={() => onChange({ issue: undefined })}
        onOpenIssue={(issue) => onChange({ issue })}
        onOpenTrace={(trace) => onChange({ trace })}
        onTrain={setTraining}
        onViewTraces={(id) =>
          onChange({ group: "trace", issue: undefined, issueFilter: id, page: 1 })
        }
        range={range}
      />

      <TrainDialog
        capability={training?.capability}
        capabilityName={training?.capabilityName}
        onClose={() => setTraining(null)}
        projectId={projectId}
        range={range}
      />

      <Sheet
        modal={false}
        onOpenChange={(open) => !open && onChange({ trace: undefined })}
        open={!!search.trace}
      >
        <SheetContent
          blurBackgroundOnHover
          onInteractOutside={(event) => event.preventDefault()}
          onOpenAutoFocus={(event) => event.preventDefault()}
          showCloseButton={false}
          showOverlay={false}
          side="right"
          size={search.detailExpanded ? "viewport" : "inspector"}
        >
          <SheetTitle className="sr-only">Trace detail</SheetTitle>
          <SheetDescription className="sr-only">
            Spans, timings and payloads for the selected trace.
          </SheetDescription>
          <SheetBody className="flex flex-col overflow-hidden p-0">
            {search.trace && (
              <TraceRun
                detailExpanded={search.detailExpanded}
                onClose={() => onChange({ trace: undefined })}
                onOpenIssue={(issue) => onChange({ issue, trace: undefined })}
                onToggleExpanded={() => onChange({ detailExpanded: !search.detailExpanded })}
                onTrain={(capability) =>
                  setTraining({
                    capability,
                    capabilityName:
                      capabilities.data?.capabilities.find((item) => item.capability === capability)
                        ?.capabilityName ?? "this capability",
                  })
                }
                projectId={projectId}
                traceId={search.trace}
              />
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
    </PageShell>
  );
}
