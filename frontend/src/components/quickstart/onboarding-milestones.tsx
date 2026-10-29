import { useEffect, useRef, useState } from "react";

import { CodingAgentIcon } from "@/components/coding-agent-icon";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/spinner";
import { useOnboardingMilestones } from "@/hooks/use-activity";
import { cn } from "@/lib/utils";
import type { OnboardingMilestone } from "@/openapi";

function StageLabel({ text, active }: { text: string; active: boolean }) {
  const [length, setLength] = useState(0);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      clearInterval(timer);
      if (preference.matches) {
        setLength(text.length);
        return;
      }
      setLength(0);
      let position = 0;
      timer = setInterval(() => {
        position += 1;
        setLength(position);
        if (position >= text.length) clearInterval(timer);
      }, 24);
    };
    start();
    preference.addEventListener("change", start);
    return () => {
      clearInterval(timer);
      preference.removeEventListener("change", start);
    };
  }, [text]);
  return (
    <h2
      aria-current={active ? "step" : undefined}
      className="relative shrink-0 whitespace-nowrap pr-3 text-sm font-medium"
    >
      <span className="sr-only">{text}</span>
      <span aria-hidden="true" className="invisible">
        {text}
      </span>
      <span aria-hidden="true" className="absolute inset-0">
        {text.slice(0, length)}
        {active && (
          <span className="ml-1 inline-block h-4 w-2 translate-y-0.5 bg-foreground motion-safe:animate-[blink_1s_step-end_infinite]" />
        )}
      </span>
    </h2>
  );
}

function CapabilityTicker({ value }: { value: number }) {
  const [displayed, setDisplayed] = useState(0);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDisplayed(value));
    return () => cancelAnimationFrame(frame);
  }, [value]);
  const digits = String(displayed).padStart(String(value).length, "0");
  return (
    <span className="inline-flex h-5 overflow-hidden font-mono text-sm tabular-nums">
      <span className="sr-only">
        {value}
        {value >= 20 ? "+" : ""}
      </span>
      {Array.from(digits, (digit, index) => (
        <span
          aria-hidden="true"
          className="inline-block w-3 overflow-hidden"
          key={digits.length - index}
        >
          <span
            className="flex flex-col transition-transform duration-700 ease-out motion-reduce:transition-none"
            style={{ transform: `translateY(-${Number(digit) * 10}%)` }}
          >
            {Array.from({ length: 10 }, (_, number) => (
              <span className="h-5 shrink-0 text-center leading-5" key={number}>
                {number}
              </span>
            ))}
          </span>
        </span>
      ))}
      {value >= 20 && <span aria-hidden="true">+</span>}
    </span>
  );
}

export function OnboardingMilestones({
  projectId,
  projectName,
  devPreview = false,
  onComplete,
  autoPlay = false,
}: {
  projectId?: string;
  projectName?: string;
  devPreview?: boolean;
  onComplete?: () => void;
  autoPlay?: boolean;
}) {
  const query = useOnboardingMilestones(projectId, "agent");
  const [simulation, setSimulation] = useState<OnboardingMilestone[] | null>(null);
  const [playing, setPlaying] = useState(false);
  const steps = simulation ?? query.data;
  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoPlay && devPreview && query.data && !autoStarted.current) {
      autoStarted.current = true;
      setSimulation(
        query.data.map((step) => ({ ...step, activityId: null, assets: [], completedAt: null }))
      );
      setPlaying(true);
    }
  }, [autoPlay, devPreview, query.data]);
  const complete = !!steps?.length && steps.every((step) => step.completedAt);
  useEffect(() => {
    if (complete && (!devPreview || simulation)) onComplete?.();
  }, [complete, devPreview, simulation, onComplete]);
  useEffect(() => {
    if (!playing || !simulation) return;
    const next = simulation.findIndex((step) => !step.completedAt);
    if (next === -1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setSimulation(
        (previous) =>
          previous?.map((step, index) =>
            index === next
              ? {
                  ...step,
                  assets: ["capabilities_discovered", "repository_synced"].includes(step.kind)
                    ? ["Answer questions", "Search knowledge", "Escalate requests"].map(
                        (label, i) => ({ id: `preview-${i}`, kind: "capability" as const, label })
                      )
                    : [],
                  completedAt: new Date(),
                }
              : step
          ) ?? null
      );
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [playing, simulation]);

  if (!steps && query.isLoading) return <LoadingState label="Loading setup progress" />;
  if (!steps) {
    return query.error ? <Alert variant="destructive">Couldn’t load setup progress.</Alert> : null;
  }

  const completed = steps.filter((step) => step.completedAt).length;
  const current = steps.find((step) => !step.completedAt);
  const stage = current?.label ?? "Your agent is set up";
  const reporter = simulation
    ? { actorKind: "codex", actorName: "Codex" }
    : current?.actorName
      ? current
      : steps.find((step) => step.actorName);
  const connected = steps.some((step) => step.kind === "agent_connected" && step.completedAt);
  const synced = steps.some((step) => step.kind === "repository_synced" && step.completedAt);
  const capabilities = new Map(
    steps
      .filter((step) => step.completedAt)
      .flatMap((step) =>
        step.assets
          .filter((asset) => asset.kind === "capability")
          .map((asset) => [asset.id, asset.label] as const)
      )
  );
  const name = connected
    ? simulation
      ? "Support Copilot"
      : (projectName ?? "Connected")
    : "Waiting for connection";

  return (
    <div className="space-y-2">
      {import.meta.env.DEV && devPreview && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>{simulation ? "Simulated onboarding · sample data" : "Dev preview"}</span>
          <div className="flex gap-2">
            {simulation && (
              <Button
                onClick={() => {
                  setPlaying(false);
                  setSimulation(null);
                }}
                size="xs"
                variant="secondary"
              >
                Live data
              </Button>
            )}
            <Button
              disabled={playing}
              onClick={() => {
                setSimulation(
                  query.data!.map((step) => ({
                    ...step,
                    activityId: null,
                    assets: [],
                    completedAt: null,
                  }))
                );
                setPlaying(true);
              }}
              size="xs"
              variant="secondary"
            >
              {playing ? "Playing…" : simulation ? "Replay" : "Play full run"}
            </Button>
          </div>
        </div>
      )}
      <Card className="space-y-3 p-4">
        <div
          aria-atomic="true"
          aria-live="polite"
          className="flex flex-wrap items-center justify-between gap-3"
        >
          <StageLabel active={!!current} key={stage} text={stage} />
          {reporter?.actorName && reporter.actorKind && (
            <span
              aria-label="Onboarding agent"
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
            >
              <CodingAgentIcon kind={reporter.actorKind} />
              {reporter.actorName}
            </span>
          )}
        </div>
        <div
          aria-label="Onboarding progress"
          aria-valuemax={steps.length}
          aria-valuemin={0}
          aria-valuenow={completed}
          aria-valuetext={`${completed} of ${steps.length} stages complete`}
          className="flex gap-1 border border-border bg-background p-1"
          role="progressbar"
        >
          {steps.flatMap((step, index) =>
            [0, 1].map((half) => (
              <span
                aria-hidden="true"
                className={cn(
                  "h-3 min-w-0 flex-1 transition-colors duration-200 motion-reduce:transition-none",
                  index < completed ? "bg-primary" : "bg-muted"
                )}
                key={`${step.kind}:${half}`}
              />
            ))
          )}
        </div>
      </Card>
      <Card aria-label="Onboarding results" className="text-xs">
        <div className="flex items-baseline justify-between gap-3 border-b border-border/70 px-4 py-3">
          <span className="text-muted-foreground">Project</span>
          <span
            className={cn("min-w-0 break-words text-right", !connected && "text-muted-foreground")}
            key={name}
          >
            {name}
          </span>
        </div>
        <dl className="divide-y divide-border/70">
          <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <dt className="text-muted-foreground">Capabilities</dt>
            <dd className="flex h-5 items-center">
              {capabilities.size ? (
                <CapabilityTicker value={capabilities.size} />
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <dt className="text-muted-foreground">Repository sync</dt>
            <dd className={cn(synced ? "text-success" : "text-muted-foreground")}>
              {synced ? "Saved" : "Pending"}
            </dd>
          </div>
        </dl>
      </Card>
      {query.error && !simulation && (
        <Alert variant="destructive">
          Couldn’t refresh progress. Showing the last saved update.
        </Alert>
      )}
    </div>
  );
}
