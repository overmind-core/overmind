import { useState } from "react";

import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { EntityRef } from "@/components/entity-ref";
import { SignalChecks } from "@/components/traces/signal-checks";
import { SignalDetailSheet, signalTraceIds } from "@/components/traces/signal-detail";
import { SignalInsights } from "@/components/traces/signal-insights";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuthContext } from "@/contexts/auth-context";
import { useGuestGate } from "@/hooks/use-guest-gate";
import {
  useMarkSignalsSeenMutation,
  useSignalCatalogQuery,
  useSignalEventsQuery,
  useSignalSubscriptionsQuery,
} from "@/hooks/use-signals";
import { featureFlags } from "@/lib/feature-flags";
import { humanizeKey } from "@/lib/label-case";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { SignalEvent } from "@/openapi";

const ALL = "__all__";

export function SignalEvents({
  projectId,
  session,
  onSelect,
  onInspectTrace,
  signal,
  capability,
  unseen,
  page,
  onChange,
}: {
  projectId: string;
  session?: string;
  onSelect: (id: string, conversation?: string | null) => void;
  onInspectTrace: (id: string, traceId: string) => void;
  signal: string | undefined;
  capability: string | undefined;
  unseen: boolean;
  page: number;
  onChange: (updates: {
    signal?: string;
    signal_capability?: string;
    unseen?: boolean;
    page?: number;
  }) => void;
}) {
  const catalog = useSignalCatalogQuery();
  const events = useSignalEventsQuery(projectId, {
    capability,
    page,
    session,
    signalType: signal,
    unseen,
  });
  const markSeen = useMarkSignalsSeenMutation(projectId);
  const guard = useGuestGate();
  const { isGuest } = useAuthContext();
  const subscriptions = useSignalSubscriptionsQuery(projectId);
  const enabledCount = catalog.data?.filter((entry) => {
    const override = subscriptions.data?.results.find(
      (sub) => !sub.capability && sub.signalType === entry.signalType
    );
    return (
      (override?.enabled ?? entry.defaultEnabled) ||
      subscriptions.data?.results.some(
        (sub) => sub.capability && sub.signalType === entry.signalType && sub.enabled
      )
    );
  }).length;
  const rows = events.data?.results ?? [];
  const count = events.data?.count ?? 0;
  const [openId, setOpenId] = useState<string | null>(null);
  const [datasetFrom, setDatasetFrom] = useState<{
    capability?: string;
    traceIds: string[];
  } | null>(null);
  const open = rows.find((event) => event.id === openId) ?? null;
  const openEvent = (event: SignalEvent) => {
    setOpenId(event.id);
    if (!event.seenAt && !isGuest) markSeen.mutate([event.id]);
  };

  return (
    <div className="flex flex-col gap-3">
      <SignalInsights
        focus={{ capability, signal }}
        onCreateDataset={
          featureFlags.datasets
            ? (insight) =>
                setDatasetFrom({
                  capability: insight.capability ?? undefined,
                  traceIds: insight.traceIds,
                })
            : undefined
        }
        onFocus={(insight) =>
          onChange({
            page: 1,
            signal: insight?.signalType,
            signal_capability: insight?.capability ?? undefined,
          })
        }
        projectId={projectId}
        session={session}
      />
      <h2 className={cn(LABEL.pixel, "pt-2 text-xs text-muted-foreground")}>Events</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          onValueChange={(v) =>
            onChange({ page: 1, signal: v === ALL ? undefined : v, signal_capability: undefined })
          }
          value={signal ?? ALL}
        >
          <SelectTrigger aria-label="Filter by signal" className="w-56" size="default">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All signals</SelectItem>
            {(catalog.data ?? []).map((entry) => (
              <SelectItem key={entry.signalType} value={entry.signalType}>
                {humanizeKey(entry.signalType)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch
            checked={unseen}
            onCheckedChange={(checked) => onChange({ page: 1, unseen: checked || undefined })}
            size="sm"
          />
          Unseen only
        </label>
        {capability && (
          <Button
            onClick={() => onChange({ page: 1, signal_capability: undefined })}
            size="xs"
            variant="secondary"
          >
            {rows[0]?.capabilityName || "Capability"}
            <Icon.close />
          </Button>
        )}
        <span className="font-mono text-xs tabular-nums text-muted-foreground">{count} events</span>
        <Button
          className="ml-auto"
          disabled={markSeen.isPending || !rows.some((event) => !event.seenAt)}
          onClick={guard(() =>
            markSeen.mutate(rows.filter((event) => !event.seenAt).map((event) => event.id))
          )}
          size="sm"
          variant="secondary"
        >
          <Icon.success />
          Mark page seen
        </Button>
        <SignalChecks projectId={projectId} />
      </div>
      {events.isLoading ? (
        <LoadingState />
      ) : events.isError ? (
        <EmptyState
          action={
            <Button onClick={() => events.refetch()} variant="secondary">
              Retry
            </Button>
          }
          description="Try again to load recorded findings."
          size="section"
          title="Couldn't load signals"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          className="rounded-md border border-dashed bg-wash-subtle"
          description={
            signal || unseen || capability
              ? "No events match these filters."
              : enabledCount === 0
                ? "Enable checks using Signal checks above."
                : "Automatic checks inspect recent traces every five minutes. An empty list does not confirm that every trace has been checked."
          }
          icon={Icon.bell}
          size="section"
          title={enabledCount === 0 ? "Signal checks are disabled" : "No recorded signals"}
        />
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">Signal</TableHead>
                <TableHead>Message</TableHead>
                <TableHead className="w-40">Capability</TableHead>
                <TableHead className="w-36">Evidence</TableHead>
                <TableHead className="w-40">Occurred</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((event) => (
                <TableRow className={cn(!event.seenAt && "bg-wash-subtle")} key={event.id}>
                  <TableCell>
                    <span className="flex items-center gap-1.5">
                      {!event.seenAt && (
                        <span aria-label="Unseen" className="size-1.5 rounded-xs bg-primary" />
                      )}
                      <Badge size="chip" variant="neutral">
                        {humanizeKey(event.signalType)}
                      </Badge>
                    </span>
                  </TableCell>
                  <TableCell className="max-w-md text-sm">
                    <button
                      className="cursor-pointer text-left hover:underline"
                      onClick={() => openEvent(event)}
                      type="button"
                    >
                      {event.message}
                    </button>
                  </TableCell>
                  <TableCell>
                    {event.capability ? (
                      <EntityRef
                        id={event.capability}
                        kind="capability"
                        name={event.capabilityName}
                      />
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Button
                      onClick={() => onSelect(event.id, event.conversation)}
                      size="sm"
                      variant="secondary"
                    >
                      {signalTraceIds(event).length}{" "}
                      {signalTraceIds(event).length === 1 ? "trace" : "traces"}
                      <Icon.chevronRight />
                    </Button>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    <DateTime value={event.occurredAt} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <SignalDetailSheet
        event={open}
        onClose={() => setOpenId(null)}
        onCreateDataset={
          featureFlags.datasets
            ? (event) =>
                setDatasetFrom({
                  capability: event.capability ?? undefined,
                  traceIds: signalTraceIds(event),
                })
            : undefined
        }
        onInspectTrace={(event, traceId) => onInspectTrace(event.id, traceId)}
        onViewTraces={(event) => onSelect(event.id, event.conversation)}
        projectId={projectId}
      />
      {featureFlags.datasets && (
        <NewDatasetDialog
          initialCapabilityId={datasetFrom?.capability}
          initialTraceIds={datasetFrom?.traceIds}
          onOpenChange={(next) => !next && setDatasetFrom(null)}
          open={!!datasetFrom}
          projectId={projectId}
        />
      )}
      {(events.data?.previous || events.data?.next) && (
        <div className="flex items-center justify-end gap-2">
          <Button
            disabled={!events.data?.previous}
            onClick={() => onChange({ page: page - 1 })}
            size="sm"
            variant="secondary"
          >
            Previous
          </Button>
          <span className="font-mono text-xs tabular-nums text-muted-foreground">Page {page}</span>
          <Button
            disabled={!events.data?.next}
            onClick={() => onChange({ page: page + 1 })}
            size="sm"
            variant="secondary"
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
