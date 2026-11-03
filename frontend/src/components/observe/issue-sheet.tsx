import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { lazyChart } from "@/components/ui/lazy-chart";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { LoadingState } from "@/components/ui/spinner";
import { useIssueDetail, useIssueState } from "@/hooks/use-issues";
import { formatNumber } from "@/lib/formatters";
import { LABEL, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { IssueDetail } from "@/openapi";
import { formatRate, SeverityBadge, stateLabel } from "./issue-parts";

const TrendChart = lazyChart(() => import("@/components/observe/trend-chart"), { minHeight: 160 });

function Heading({ children }: { children: string }) {
  return <h3 className={cn(LABEL.pixel, "text-xs text-muted-foreground")}>{children}</h3>;
}

function Body({
  issue,
  range,
  onViewTraces,
  onOpenTrace,
  onOpenIssue,
  onTrain,
}: {
  issue: IssueDetail;
  range: string;
  onViewTraces: () => void;
  onOpenTrace: (traceId: string) => void;
  onOpenIssue: (id: string) => void;
  onTrain: (signal: { capability: string; capabilityName: string }) => void;
}) {
  const state = useIssueState(issue.id);
  const stopped = issue.state === "muted" || issue.state === "resolved";
  const trainable = issue.cause === "model" && issue.capability;

  return (
    <div className="flex flex-col gap-6 p-6">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={issue.severity} />
          <Badge size="chip" variant="neutral">
            {stateLabel(issue.state)}
          </Badge>
        </div>
        <SheetTitle className={TITLE.card}>{issue.title}</SheetTitle>
        <SheetDescription className="text-sm tabular-nums text-muted-foreground">
          {issue.capabilityName && `${issue.capabilityName} · `}
          {formatRate(issue.rate)} of traces · {formatNumber(issue.traces)} traces in {range}
          {issue.sessions > 0 && ` · ${formatNumber(issue.sessions)} sessions`}
        </SheetDescription>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {trainable ? (
          <Button
            onClick={() =>
              onTrain({
                capability: issue.capability as string,
                capabilityName: issue.capabilityName,
              })
            }
          >
            <Icon.training />
            Train a model
          </Button>
        ) : (
          issue.remedy && <p className="text-sm text-foreground">{issue.remedy}</p>
        )}
        <Button onClick={onViewTraces} variant="secondary">
          <Icon.observability />
          View traces
        </Button>
        <span className="ml-auto flex gap-2">
          {stopped ? (
            <Button onClick={() => state.mutate("reopen")} size="xs" variant="secondary">
              Reopen
            </Button>
          ) : (
            <>
              <Button onClick={() => state.mutate("resolve")} size="xs" variant="secondary">
                Resolve
              </Button>
              <Button onClick={() => state.mutate("mute")} size="xs" variant="secondary">
                Mute
              </Button>
            </>
          )}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <Heading>Traces per day, last 30 days</Heading>
        <TrendChart
          bucket="day"
          color="var(--destructive)"
          data={issue.buckets.map((at, index) => ({
            at: new Date(at).getTime(),
            value: issue.trend[index] ?? 0,
          }))}
          format={(value) => formatNumber(value)}
          height={160}
          label="Traces"
          markers={issue.markers}
        />
      </div>

      {issue.related.length > 0 && (
        <div className="flex flex-col gap-2">
          <Heading>Same failure elsewhere</Heading>
          <ul className="divide-y divide-border/70 rounded-md border border-border bg-card">
            {issue.related.map((other) => (
              <li key={other.id}>
                <button
                  className="grid w-full grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-left text-xs hover:bg-wash-raised focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => onOpenIssue(other.id)}
                  type="button"
                >
                  <span className="tabular-nums text-foreground">{formatRate(other.rate)}</span>
                  <span className="truncate text-foreground">{other.capabilityName}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {formatNumber(other.traces)} traces
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {issue.examples.length > 0 && (
        <div className="flex flex-col gap-2">
          <Heading>Examples</Heading>
          <ul className="flex flex-col gap-2">
            {issue.examples.map((example) => (
              <li key={example.traceId}>
                <button
                  className="flex w-full flex-col gap-1.5 rounded-md border border-border bg-card p-3 text-left text-xs hover:bg-wash-raised"
                  onClick={() => onOpenTrace(example.traceId)}
                  type="button"
                >
                  <span className="flex justify-between gap-3 text-muted-foreground">
                    <span className="truncate">{example.reason}</span>
                    <DateTime value={example.occurredAt} />
                  </span>
                  {example.input && (
                    <span className="line-clamp-2 text-foreground">{example.input}</span>
                  )}
                  {example.output && (
                    <span className="line-clamp-2 text-muted-foreground">{example.output}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function IssueSheet({
  issueId,
  range,
  onClose,
  onViewTraces,
  onOpenTrace,
  onOpenIssue,
  onTrain,
}: {
  issueId?: string;
  range: string;
  onClose: () => void;
  onViewTraces: (issueId: string) => void;
  onOpenTrace: (traceId: string) => void;
  onOpenIssue: (id: string) => void;
  onTrain: (signal: { capability: string; capabilityName: string }) => void;
}) {
  const { data } = useIssueDetail(issueId, range);
  return (
    <Sheet onOpenChange={(open) => !open && onClose()} open={!!issueId}>
      <SheetContent side="right" size="lg">
        <SheetBody className="overflow-y-auto p-0">
          {data && data.id === issueId ? (
            <Body
              issue={data}
              onOpenIssue={onOpenIssue}
              onOpenTrace={onOpenTrace}
              onTrain={onTrain}
              onViewTraces={() => onViewTraces(data.id)}
              range={range}
            />
          ) : (
            <>
              <SheetTitle className="sr-only">Issue</SheetTitle>
              <SheetDescription className="sr-only">Loading the issue.</SheetDescription>
              <LoadingState label="Loading issue" />
            </>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
