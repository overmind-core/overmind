import { useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
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
import { useGuestGate } from "@/hooks/use-guest-gate";
import {
  useCreateSignalSubscriptionMutation,
  useMarkSignalsSeenMutation,
  useSignalCatalogQuery,
  useSignalEventsQuery,
  useSignalSubscriptionsQuery,
  useUpdateSignalSubscriptionMutation,
} from "@/hooks/use-signals";
import { humanizeKey } from "@/lib/label-case";
import { cn } from "@/lib/utils";
import type { SignalCatalogEntry, SignalSubscription, SignalTypeEnum } from "@/openapi";

const ALL = "__all__";

function SignalChecks({ projectId }: { projectId: string }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Icon.settings />
          Signal checks
        </Button>
      </DialogTrigger>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Signal checks</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="mb-4 text-sm text-muted-foreground">
            Checks run automatically every five minutes over the last 24 hours of received traces.
            Disable noisy checks or adjust their thresholds here. Plan drift is off by default
            because it makes additional model calls.
          </p>
          <SubscriptionsView projectId={projectId} />
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

export function SignalEvents({
  projectId,
  session,
  onSelect,
  signal,
  unseen,
  page,
  onChange,
}: {
  projectId: string;
  session?: string;
  onSelect: (id: string, conversation?: string | null) => void;
  signal: string | undefined;
  unseen: boolean;
  page: number;
  onChange: (updates: { signal?: string; unseen?: boolean; page?: number }) => void;
}) {
  const catalog = useSignalCatalogQuery();
  const events = useSignalEventsQuery(projectId, { page, session, signalType: signal, unseen });
  const markSeen = useMarkSignalsSeenMutation(projectId);
  const guard = useGuestGate();
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

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          onValueChange={(v) => onChange({ page: 1, signal: v === ALL ? undefined : v })}
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
            signal || unseen
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
                      onClick={() => onSelect(event.id, event.conversation)}
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
                      View traces
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

function ExpandableText({ text, title }: { text: string; title: string }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          aria-label={`Show full text of ${title}`}
          className="line-clamp-2 cursor-pointer whitespace-normal text-left hover:text-foreground"
          type="button"
        >
          {text}
        </button>
      </DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="whitespace-pre-wrap text-sm">{text}</p>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function SubscriptionsView({ projectId }: { projectId: string }) {
  const catalog = useSignalCatalogQuery();
  const subscriptions = useSignalSubscriptionsQuery(projectId);
  if (catalog.isLoading || subscriptions.isLoading) return <LoadingState />;
  const byType = new Map(
    (subscriptions.data?.results ?? [])
      .filter((sub) => !sub.capability)
      .map((sub) => [sub.signalType, sub])
  );
  return (
    <div className="overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-48">Signal</TableHead>
            <TableHead>Detects</TableHead>
            <TableHead className="w-72">Parameters</TableHead>
            <TableHead className="w-20 text-center">Enabled</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(catalog.data ?? []).map((entry) => (
            <SubscriptionRow
              entry={entry}
              key={entry.signalType}
              projectId={projectId}
              subscription={byType.get(entry.signalType as SignalTypeEnum)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function SubscriptionRow({
  entry,
  subscription,
  projectId,
}: {
  entry: SignalCatalogEntry;
  subscription: SignalSubscription | undefined;
  projectId: string;
}) {
  const create = useCreateSignalSubscriptionMutation(projectId);
  const update = useUpdateSignalSubscriptionMutation(projectId);
  const guard = useGuestGate();
  const config = (subscription?.config ?? {}) as Record<string, unknown>;
  const params = Object.entries(entry.params ?? {});

  const save = (body: { enabled?: boolean; config?: Record<string, unknown> }) => {
    if (subscription) update.mutate({ body, id: subscription.id });
    else
      create.mutate({
        config: body.config ?? {},
        enabled: body.enabled ?? entry.defaultEnabled,
        project: projectId,
        signalType: entry.signalType as SignalTypeEnum,
      });
  };

  return (
    <TableRow>
      <TableCell className="text-sm">{humanizeKey(entry.signalType)}</TableCell>
      <TableCell className="max-w-md text-sm text-muted-foreground">
        <ExpandableText text={entry.description} title={humanizeKey(entry.signalType)} />
      </TableCell>
      <TableCell>
        {params.length === 0 ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <div className="flex flex-wrap gap-2">
            {params.map(([name, fallback]) => (
              <ParamInput
                defaultValue={fallback}
                key={name}
                name={name}
                onCommit={(value) => {
                  const next = { ...config };
                  if (value === undefined) delete next[name];
                  else next[name] = value;
                  save({ config: next });
                }}
                value={config[name]}
              />
            ))}
          </div>
        )}
      </TableCell>
      <TableCell className="text-center">
        <Switch
          aria-label={`Toggle ${entry.signalType}`}
          checked={subscription?.enabled ?? entry.defaultEnabled}
          disabled={create.isPending || update.isPending}
          onCheckedChange={guard((enabled: boolean) => save({ config, enabled }))}
          size="sm"
        />
      </TableCell>
    </TableRow>
  );
}

function ParamInput({
  name,
  value,
  defaultValue,
  onCommit,
}: {
  name: string;
  value: unknown;
  defaultValue: unknown;
  onCommit: (value: number | undefined) => void;
}) {
  const [draft, setDraft] = useState(value == null ? "" : String(value));
  const commit = () => {
    const trimmed = draft.trim();
    const next = trimmed === "" ? undefined : Number(trimmed);
    if (next !== undefined && !Number.isFinite(next)) return;
    if (next === (value ?? undefined)) return;
    onCommit(next);
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {name}
      <Input
        className="w-20 font-mono"
        inputMode="decimal"
        onBlur={commit}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
        placeholder={String(defaultValue ?? "")}
        size="xs"
        value={draft}
      />
    </label>
  );
}
