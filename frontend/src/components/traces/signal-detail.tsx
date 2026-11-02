import { Link } from "@tanstack/react-router";

import { EntityRef } from "@/components/entity-ref";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { humanizeKey } from "@/lib/label-case";
import { TITLE } from "@/lib/typography";
import type { SignalEvent } from "@/openapi";

type FailedMetric = {
  name: string;
  expectation: string;
  score: number | null;
  verdict: string;
  reason: string;
};

export type SignalEvidence = {
  span_id: string;
  trace_id: string;
  name: string;
  span_type: string;
  status: "ok" | "error";
  status_message: string;
  model: string | null;
  tool: string | null;
  duration_ms: number;
  tokens: number | null;
  cost: number | null;
  input: string;
  output: string;
  intent: string;
  score: number | null;
  drift: number | null;
  failed_metrics: FailedMetric[];
  reason: string;
};

export type SignalAction = {
  kind: "fix" | "inspect_traces" | "create_dataset" | "review_evaluations" | "open_optimiser";
  label: string;
};

export type SignalDetails = {
  spans?: number;
  group?: string;
  trace_ids?: string[];
  group_trace_ids?: string[];
  span_ids?: string[];
  why?: string;
  parameters?: Record<string, unknown>;
  measurements?: Record<string, unknown>;
  evidence?: SignalEvidence[];
  evidence_truncated?: boolean;
  recommended_actions?: SignalAction[];
};

export function signalDetails(event: SignalEvent): SignalDetails {
  return (event.details ?? {}) as SignalDetails;
}

export function signalTraceIds(event: SignalEvent): string[] {
  const ids = signalDetails(event).trace_ids;
  return ids?.length ? ids : event.traceId ? [event.traceId] : [];
}

function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number")
    return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, "");
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function Facts({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (!entries.length) return null;
  return (
    <section className="flex flex-col gap-2">
      <h3 className={TITLE.card}>{title}</h3>
      <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-4 gap-y-1 text-sm">
        {entries.map(([key, value]) => (
          <div className="contents" key={key}>
            <dt className="text-muted-foreground">{humanizeKey(key)}</dt>
            <dd className="break-words font-mono text-xs tabular-nums">{formatValue(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Excerpt({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <p className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-sm border border-border/70 bg-wash-subtle px-2 py-1.5 font-mono text-xs">
        {text}
      </p>
    </div>
  );
}

function EvidenceCard({
  item,
  onInspectTrace,
}: {
  item: SignalEvidence;
  onInspectTrace: (traceId: string) => void;
}) {
  return (
    <li className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate text-sm text-foreground">{item.name}</span>
        <Badge size="chip" variant="neutral">
          {humanizeKey(item.span_type)}
        </Badge>
        {item.status === "error" && (
          <Badge size="chip" variant="error">
            Error
          </Badge>
        )}
        {item.score != null && (
          <Badge size="chip" variant={item.score < 0.5 ? "error" : "neutral"}>
            {item.score.toFixed(2)}
          </Badge>
        )}
        <Button
          className="ml-auto"
          onClick={() => onInspectTrace(item.trace_id)}
          size="sm"
          variant="secondary"
        >
          Inspect trace
          <Icon.chevronRight />
        </Button>
      </div>
      <p className="text-sm">{item.reason}</p>
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-muted-foreground tabular-nums">
        {item.model && <span>{item.model}</span>}
        {item.tool && <span>tool {item.tool}</span>}
        <span>{item.duration_ms} ms</span>
        {item.tokens != null && <span>{item.tokens} tokens</span>}
        {item.cost != null && <span>${item.cost}</span>}
      </div>
      {item.failed_metrics.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs">
          {item.failed_metrics.map((metric) => (
            <li key={metric.name}>
              <span className="text-destructive">
                {metric.expectation || metric.name}
                {metric.score != null && ` · ${metric.score.toFixed(2)}`}
              </span>
              {metric.reason && <span className="text-muted-foreground"> — {metric.reason}</span>}
            </li>
          ))}
        </ul>
      )}
      <Excerpt label="Intent" text={item.intent} />
      <Excerpt label="Input" text={item.input} />
      <Excerpt label="Output" text={item.output} />
    </li>
  );
}

export function SignalDetailSheet({
  event,
  projectId,
  onClose,
  onViewTraces,
  onInspectTrace,
  onCreateDataset,
}: {
  event: SignalEvent | null;
  projectId: string;
  onClose: () => void;
  onViewTraces: (event: SignalEvent) => void;
  onInspectTrace: (event: SignalEvent, traceId: string) => void;
  onCreateDataset?: (event: SignalEvent) => void;
}) {
  const details = event ? signalDetails(event) : {};
  const traceCount = event ? signalTraceIds(event).length : 0;
  const evidence = details.evidence ?? [];
  const guidance = (details.recommended_actions ?? []).filter((action) => action.kind === "fix");

  return (
    <Sheet onOpenChange={(open) => !open && onClose()} open={!!event}>
      <SheetContent size="lg">
        {event && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                <Badge size="chip" variant="neutral">
                  {humanizeKey(event.signalType)}
                </Badge>
                <span className="min-w-0 truncate">{event.message}</span>
              </SheetTitle>
              <SheetDescription className="flex flex-wrap items-center gap-x-3 text-xs">
                <DateTime value={event.occurredAt} />
                {event.capability && (
                  <EntityRef id={event.capability} kind="capability" name={event.capabilityName} />
                )}
              </SheetDescription>
            </SheetHeader>
            <SheetBody className="flex flex-col gap-5">
              {details.why && <p className="text-sm">{details.why}</p>}

              <section className="flex flex-col gap-2">
                <h3 className={TITLE.card}>Actions</h3>
                {guidance.map((action) => (
                  <p className="text-sm" key={action.label}>
                    {action.label}
                  </p>
                ))}
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => onViewTraces(event)} size="sm" variant="secondary">
                    <Icon.observability />
                    View {traceCount} {traceCount === 1 ? "trace" : "traces"}
                  </Button>
                  {onCreateDataset && (
                    <Button
                      disabled={traceCount === 0}
                      onClick={() => onCreateDataset(event)}
                      size="sm"
                      variant="secondary"
                    >
                      <Icon.datasetAdd />
                      Create dataset
                    </Button>
                  )}
                  {event.capability && (
                    <Button asChild size="sm" variant="secondary">
                      <Link
                        search={{ capabilityId: event.capability, optimize: true, projectId }}
                        to="/optimiser"
                      >
                        <Icon.optimiser />
                        Open Optimiser
                      </Link>
                    </Button>
                  )}
                </div>
              </section>

              <Facts title="Thresholds" values={details.parameters ?? {}} />
              <Facts title="Measured" values={details.measurements ?? {}} />

              {evidence.length > 0 && (
                <section className="flex flex-col gap-2">
                  <h3 className={TITLE.card}>
                    Triggering spans
                    <span className="ml-2 font-mono text-xs tabular-nums text-muted-foreground">
                      {details.span_ids?.length ?? evidence.length}
                    </span>
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {evidence.map((item) => (
                      <EvidenceCard
                        item={item}
                        key={`${item.span_id}-${item.reason}`}
                        onInspectTrace={(traceId) => onInspectTrace(event, traceId)}
                      />
                    ))}
                  </ul>
                  {details.evidence_truncated && (
                    <p className="text-xs text-muted-foreground">
                      First {evidence.length} of {details.span_ids?.length} spans.
                    </p>
                  )}
                </section>
              )}
            </SheetBody>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
