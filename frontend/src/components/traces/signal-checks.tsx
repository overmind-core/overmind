import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { LoadingState } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { useGuestGate } from "@/hooks/use-guest-gate";
import {
  useCreateSignalSubscriptionMutation,
  useSignalCatalogQuery,
  useSignalSubscriptionsQuery,
  useUpdateSignalSubscriptionMutation,
} from "@/hooks/use-signals";
import { humanizeKey } from "@/lib/label-case";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { SignalCatalogEntry, SignalSubscription, SignalTypeEnum } from "@/openapi";

const GROUPS: [string, string[]][] = [
  ["Scores", ["low_scores", "score_drop", "repeated_failure", "hidden_failure", "unscorable"]],
  ["Errors", ["errors", "tool_failure", "retry_storm", "swallowed_error", "abandoned"]],
  ["Agent behaviour", ["loop", "runaway_trace", "plan_drift", "hallucinated_tool"]],
  ["Sessions", ["abandoned_session", "repeated_input", "frustration", "context_overflow"]],
  ["Latency and cost", ["slow_llm", "token_spike", "cost_spike", "model_change"]],
  ["Outputs", ["leak", "refusal", "broken_json"]],
];

const MODEL_CALLS = new Set(["plan_drift"]);

function grouped(entries: SignalCatalogEntry[]) {
  const known = new Set(GROUPS.flatMap(([, types]) => types));
  const groups = GROUPS.map(
    ([title, types]) =>
      [title, entries.filter((entry) => types.includes(entry.signalType))] as const
  );
  return [
    ...groups,
    ["Other", entries.filter((entry) => !known.has(entry.signalType))] as const,
  ].filter(([, items]) => items.length > 0);
}

export function SignalChecks({ projectId }: { projectId: string }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <Icon.settings />
          Signal checks
        </Button>
      </DialogTrigger>
      <DialogContent size="lg">
        <SignalChecksContent projectId={projectId} />
      </DialogContent>
    </Dialog>
  );
}

function SignalChecksContent({ projectId }: { projectId: string }) {
  const catalog = useSignalCatalogQuery();
  const subscriptions = useSignalSubscriptionsQuery(projectId);
  const [query, setQuery] = useState("");
  const byType = new Map(
    (subscriptions.data?.results ?? [])
      .filter((sub) => !sub.capability)
      .map((sub) => [sub.signalType, sub])
  );
  const entries = catalog.data ?? [];
  const enabled = (entry: SignalCatalogEntry) =>
    byType.get(entry.signalType as SignalTypeEnum)?.enabled ?? entry.defaultEnabled;
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? entries.filter((entry) =>
        `${humanizeKey(entry.signalType)} ${entry.description}`.toLowerCase().includes(needle)
      )
    : entries;

  return (
    <>
      <DialogHeader
        end={
          <SearchInput
            className="w-48"
            label="Filter checks"
            onChange={(e) => setQuery(e.target.value)}
            onClear={() => setQuery("")}
            placeholder="Filter checks"
            size="sm"
            value={query}
          />
        }
      >
        <DialogTitle>Signal checks</DialogTitle>
        <DialogDescription>
          {entries.filter(enabled).length} of {entries.length} enabled · runs every 5 min on the
          last 24 h
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-5">
        {catalog.isLoading || subscriptions.isLoading ? (
          <LoadingState />
        ) : visible.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No checks match.</p>
        ) : (
          grouped(visible).map(([title, items]) => (
            <section className="flex flex-col gap-2" key={title}>
              <h3
                className={cn(
                  LABEL.pixel,
                  "flex items-center justify-between text-xs text-muted-foreground"
                )}
              >
                {title}
                <span className="tabular-nums">
                  {items.filter(enabled).length}/{items.length}
                </span>
              </h3>
              <ul className="divide-y divide-border/70 overflow-hidden rounded-md border border-border bg-card">
                {items.map((entry) => (
                  <CheckRow
                    entry={entry}
                    key={entry.signalType}
                    projectId={projectId}
                    subscription={byType.get(entry.signalType as SignalTypeEnum)}
                  />
                ))}
              </ul>
            </section>
          ))
        )}
      </DialogBody>
    </>
  );
}

function CheckRow({
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
  const enabled = subscription?.enabled ?? entry.defaultEnabled;
  const id = `signal-check-${entry.signalType}`;

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
    <li className="flex flex-wrap items-start gap-x-4 gap-y-2 px-4 py-3">
      <Switch
        checked={enabled}
        className="mt-0.5"
        disabled={create.isPending || update.isPending}
        id={id}
        onCheckedChange={guard((next: boolean) => save({ config, enabled: next }))}
        size="sm"
      />
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
        <span className="flex items-center gap-2">
          <label
            className={cn(
              "cursor-pointer text-sm",
              enabled ? "text-foreground" : "text-muted-foreground"
            )}
            htmlFor={id}
          >
            {humanizeKey(entry.signalType)}
          </label>
          {MODEL_CALLS.has(entry.signalType) && (
            <Badge size="chip" variant="warning">
              Model calls
            </Badge>
          )}
        </span>
        <p className="text-xs text-muted-foreground">{entry.description}</p>
      </div>
      {params.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
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
    </li>
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
      {humanizeKey(name)}
      <Input
        className="w-16 font-mono tabular-nums"
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
