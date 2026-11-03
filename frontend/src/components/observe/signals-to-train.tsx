import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { formatNumber } from "@/lib/formatters";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { IssueRow, ObservabilityOverview, SystemIssue, TrainingSignal } from "@/openapi";
import {
  formatRate,
  SEVERITY_RULE,
  SEVERITY_TEXT,
  SectionHeading,
  stateLabel,
} from "./issue-parts";

type Train = (signal: { capability: string; capabilityName: string }) => void;

function Reason({ issue, onOpen }: { issue: IssueRow; onOpen: (id: string) => void }) {
  return (
    <li>
      <button
        className="grid w-full grid-cols-[6rem_minmax(0,1fr)] items-center gap-3 rounded-sm py-2.5 text-left hover:bg-wash-raised focus-visible:outline-2 focus-visible:outline-ring"
        onClick={() => onOpen(issue.id)}
        type="button"
      >
        <span className={cn("text-2xl tabular-nums", SEVERITY_TEXT[issue.severity])}>
          {formatRate(issue.rate)}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm text-foreground">{issue.title}</span>
          <span className="block text-xs text-muted-foreground">
            <span className={SEVERITY_TEXT[issue.severity]}>{issue.severity}</span>,{" "}
            {stateLabel(issue.state).toLowerCase()}, {formatNumber(issue.traces)} traces
          </span>
        </span>
      </button>
    </li>
  );
}

/** How the capability's own traffic becomes a model. Failures stay out: they record the
 *  wrong answer, so they could neither teach a model nor grade one. */
function TrainingData({ signal, onTrain }: { signal: TrainingSignal; onTrain: Train }) {
  const total = signal.failingTraces + signal.passingTraces;
  const failingShare = total ? (signal.failingTraces / total) * 100 : 0;
  return (
    <div className="flex flex-col justify-between gap-5 border-t border-border/70 bg-wash-subtle p-6 lg:border-t-0 lg:border-l">
      <div className="flex flex-col gap-4">
        <div aria-hidden="true" className="flex h-2 gap-0.5 overflow-hidden rounded-xs">
          <span className="bg-success" style={{ flexGrow: 100 - failingShare }} />
          <span className="min-w-1 bg-destructive" style={{ flexGrow: failingShare }} />
        </div>
        <dl className="grid grid-cols-2 gap-4">
          <div>
            <dt className="text-xs text-muted-foreground">Trains on</dt>
            <dd className="text-lg tabular-nums text-foreground">
              {formatNumber(signal.passingTraces)}
            </dd>
            <dd className="text-xs text-muted-foreground">passing traces</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Left out</dt>
            <dd className="text-lg tabular-nums text-foreground">
              {formatNumber(signal.failingTraces)}
            </dd>
            <dd className="text-xs text-muted-foreground">failing traces</dd>
          </div>
        </dl>
      </div>
      <div className="flex flex-col gap-2">
        <Button className="w-full" onClick={() => onTrain(signal)} size="lg">
          <Icon.training />
          Train a model
        </Button>
        {signal.model && (
          <p className="truncate text-center text-xs text-muted-foreground">
            Now on {signal.model}
          </p>
        )}
      </div>
    </div>
  );
}

function TrainNext({
  signal,
  onTrain,
  onOpen,
  onSeeIssues,
}: {
  signal: TrainingSignal;
  onTrain: Train;
  onOpen: (id: string) => void;
  onSeeIssues: (capability: string) => void;
}) {
  const total = signal.failingTraces + signal.passingTraces;
  return (
    <section
      aria-label={`Train ${signal.capabilityName} next`}
      className={cn(
        "grid overflow-hidden rounded-md border border-border border-t-2 bg-card lg:grid-cols-[minmax(0,1fr)_22rem]",
        SEVERITY_RULE[signal.issues[0]?.severity ?? "high"]
      )}
    >
      <div className="flex min-w-0 flex-col gap-3 p-6">
        <div>
          <h2 className={cn(TITLE.section, "text-foreground")}>
            Train {signal.capabilityName} next
          </h2>
          <p className="text-sm tabular-nums text-muted-foreground">
            {formatNumber(signal.failingTraces)} of {formatNumber(total)} traces failed in ways a
            model can learn.
          </p>
        </div>
        <ul className="flex flex-col">
          {signal.issues.map((issue) => (
            <Reason issue={issue} key={issue.id} onOpen={onOpen} />
          ))}
        </ul>
        {signal.moreIssues > 0 && (
          <Button
            className="self-start"
            onClick={() => onSeeIssues(signal.capability)}
            size="xs"
            variant="secondary"
          >
            {formatNumber(signal.moreIssues)} more {signal.moreIssues === 1 ? "issue" : "issues"}
          </Button>
        )}
      </div>
      <TrainingData onTrain={onTrain} signal={signal} />
    </section>
  );
}

function AlsoTrain({
  signal,
  onTrain,
  onOpen,
}: {
  signal: TrainingSignal;
  onTrain: Train;
  onOpen: (id: string) => void;
}) {
  const issue = signal.issues[0];
  return (
    <li className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3">
      <span className={cn("text-base tabular-nums", SEVERITY_TEXT[issue.severity])}>
        {formatRate(issue.rate)}
      </span>
      <button
        className="min-w-0 rounded-sm text-left hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onClick={() => onOpen(issue.id)}
        type="button"
      >
        <span className="block truncate text-sm text-foreground">{signal.capabilityName}</span>
        <span className="block truncate text-xs text-muted-foreground">{issue.title}</span>
      </button>
      <Button onClick={() => onTrain(signal)} size="sm" variant="secondary">
        <Icon.training />
        Train
      </Button>
    </li>
  );
}

function SystemLine({ issue, onOpen }: { issue: SystemIssue; onOpen: (id: string) => void }) {
  return (
    <li>
      <button
        className="grid w-full grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-3 px-4 py-3 text-left hover:bg-wash-raised focus-visible:outline-2 focus-visible:outline-ring"
        onClick={() => onOpen(issue.id)}
        type="button"
      >
        <span className={cn("text-base tabular-nums", SEVERITY_TEXT[issue.severity])}>
          {formatRate(issue.rate)}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm text-foreground">
            {issue.capabilityCount > 1
              ? `${issue.capabilityCount} capabilities`
              : issue.capabilityName}
          </span>
          <span className="block truncate text-xs text-muted-foreground">{issue.title}</span>
        </span>
      </button>
    </li>
  );
}

function More({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <li>
      <button
        className="w-full px-4 py-2.5 text-left text-xs text-muted-foreground hover:bg-wash-raised hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        onClick={onClick}
        type="button"
      >
        {label}
      </button>
    </li>
  );
}

export function WhatToKnow({
  overview,
  onTrain,
  onOpen,
  onSeeQueue,
  onSeeSystem,
  onSeeIssues,
}: {
  overview: ObservabilityOverview;
  onTrain: Train;
  onOpen: (id: string) => void;
  onSeeQueue: () => void;
  onSeeSystem: () => void;
  onSeeIssues: (capability: string) => void;
}) {
  const { trainingSignals, systemIssues, signalsToTrain, systemIssueCount } = overview;
  const [next, ...others] = trainingSignals;
  const moreSignals = signalsToTrain - trainingSignals.length;
  const moreSystem = systemIssueCount - systemIssues.length;
  const both = others.length > 0 && systemIssues.length > 0;
  return (
    <div className="flex flex-col gap-4">
      {next && (
        <TrainNext onOpen={onOpen} onSeeIssues={onSeeIssues} onTrain={onTrain} signal={next} />
      )}
      {(others.length > 0 || systemIssues.length > 0) && (
        <div className={cn("grid items-start gap-4", both && "lg:grid-cols-2")}>
          {others.length > 0 && (
            <section aria-label="Also ready to train">
              <SectionHeading>Also ready to train</SectionHeading>
              <ul className="divide-y divide-border/70 overflow-hidden rounded-md border border-border bg-card">
                {others.map((signal) => (
                  <AlsoTrain
                    key={signal.capability}
                    onOpen={onOpen}
                    onTrain={onTrain}
                    signal={signal}
                  />
                ))}
                {moreSignals > 0 && (
                  <More
                    label={`${formatNumber(moreSignals)} more ${moreSignals === 1 ? "capability" : "capabilities"} ready to train`}
                    onClick={onSeeQueue}
                  />
                )}
              </ul>
            </section>
          )}
          {systemIssues.length > 0 && (
            <section aria-label="Fix outside the model">
              <SectionHeading>Fix outside the model</SectionHeading>
              <ul className="divide-y divide-border/70 overflow-hidden rounded-md border border-border bg-card">
                {systemIssues.map((issue) => (
                  <SystemLine issue={issue} key={issue.id} onOpen={onOpen} />
                ))}
                {moreSystem > 0 && (
                  <More label={`${formatNumber(moreSystem)} more to fix`} onClick={onSeeSystem} />
                )}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
