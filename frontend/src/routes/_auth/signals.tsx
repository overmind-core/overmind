import { useState } from "react";

import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { EntityRef } from "@/components/entity-ref";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
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
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

const signalsSearchSchema = z.object({
  page: z.coerce.number().min(1).optional().default(1).catch(1),
  projectId: z.string().optional(),
  signal: z.string().optional(),
  unseen: z.boolean().optional().catch(undefined),
  view: z.enum(["events", "subscriptions"]).optional().default("events").catch("events"),
});

export const Route = createFileRoute("/_auth/signals")({
  component: SignalsPage,
  validateSearch: signalsSearchSchema,
});

const ALL = "__all__";

function SignalsPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { projectId, view } = search;
  const patch = (updates: Partial<z.infer<typeof signalsSearchSchema>>) =>
    navigate({ replace: true, resetScroll: false, search: (prev) => ({ ...prev, ...updates }) });

  const header = (
    <PageHeader
      actions={
        projectId ? (
          <Tabs onValueChange={(v) => patch({ page: 1, view: v as typeof view })} value={view}>
            <TabsList>
              <TabsTrigger value="events">Events</TabsTrigger>
              <TabsTrigger value="subscriptions">Subscriptions</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : null
      }
      description="Signals detected on production traces."
      icon={<Icon.bell aria-hidden className="size-6 shrink-0" />}
      title="Signals"
    />
  );

  if (!projectId) {
    return (
      <PageShell header={header} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={header} variant="full">
      {view === "events" ? (
        <EventsView
          onChange={patch}
          page={search.page}
          projectId={projectId}
          signal={search.signal}
          unseen={!!search.unseen}
        />
      ) : (
        <SubscriptionsView projectId={projectId} />
      )}
    </PageShell>
  );
}

function EventsView({
  projectId,
  signal,
  unseen,
  page,
  onChange,
}: {
  projectId: string;
  signal: string | undefined;
  unseen: boolean;
  page: number;
  onChange: (updates: { signal?: string; unseen?: boolean; page?: number }) => void;
}) {
  const catalog = useSignalCatalogQuery();
  const events = useSignalEventsQuery(projectId, { page, signalType: signal, unseen });
  const markSeen = useMarkSignalsSeenMutation(projectId);
  const guard = useGuestGate();
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
          disabled={markSeen.isPending}
          onClick={guard(() => markSeen.mutate(undefined))}
          size="sm"
          variant="secondary"
        >
          <Icon.success />
          Mark all seen
        </Button>
      </div>
      {events.isLoading ? (
        <LoadingState />
      ) : rows.length === 0 ? (
        <EmptyState
          className="rounded-md border border-dashed bg-wash-subtle"
          icon={Icon.bell}
          size="section"
          title="No signal events"
        />
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">Signal</TableHead>
                <TableHead>Message</TableHead>
                <TableHead className="w-40">Capability</TableHead>
                <TableHead className="w-36">Trace</TableHead>
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
                    <ExpandableText text={event.message} title={humanizeKey(event.signalType)} />
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
                    {event.traceId ? (
                      <EntityRef
                        id={event.traceId}
                        kind="trace"
                        name={event.traceId.slice(0, 12)}
                        projectId={projectId}
                      />
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
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
        enabled: body.enabled ?? true,
        project: projectId,
        signalType: entry.signalType as SignalTypeEnum,
      });
  };

  return (
    <TableRow>
      <TableCell className="font-mono text-sm">{entry.signalType}</TableCell>
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
          checked={subscription?.enabled ?? false}
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
