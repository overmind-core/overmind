import type { ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { type EvalPreloadStatus, isActiveEvalPreloadStatus } from "@/lib/eval-preload";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";

function CapabilityEvaluatorsLink({
  capabilityId,
  children,
  className,
  projectId,
}: {
  capabilityId: string;
  children: ReactNode;
  className?: string;
  projectId?: string;
}) {
  return (
    <Link
      className={className}
      params={{ capabilityId }}
      search={{ projectId, tab: "evaluators" }}
      to="/capabilities/$capabilityId"
    >
      {children}
    </Link>
  );
}

export function EvalSetEmptyWizardState({
  capabilityId,
  error,
  projectId,
  status,
  variant = "card",
}: {
  capabilityId: string;
  error?: string | null;
  projectId?: string;
  status: EvalPreloadStatus | null;
  variant?: "card" | "compact";
}) {
  if (isActiveEvalPreloadStatus(status)) {
    if (variant === "compact") {
      return (
        <div className="flex items-start gap-2 rounded-md border border-border bg-wash-raised px-2.5 py-1.5 text-xs text-muted-foreground">
          <Spinner className="mt-0.5 size-3 shrink-0" size="sm" />
          <span>Authoring Default eval set after code scan — usually ready within a minute.</span>
        </div>
      );
    }
    return (
      <Alert className="border-border bg-wash-raised text-foreground">
        <div className="flex items-start gap-2">
          <Spinner className="shrink-0" size="sm" />
          <div>
            <p className="font-medium">Authoring Default eval set</p>
            <p className={cn(PROSE, "text-sm")}>
              After your code scan we read the capability card and author deterministic checks and
              LLM judges. This usually takes under a minute.
            </p>
          </div>
        </div>
      </Alert>
    );
  }

  if (status === "failed") {
    const message = error?.trim() || "Default eval set authoring failed after retries.";
    if (variant === "compact") {
      return (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">
          {message}{" "}
          <CapabilityEvaluatorsLink
            capabilityId={capabilityId}
            className="font-medium underline"
            projectId={projectId}
          >
            Open Evaluations tab
          </CapabilityEvaluatorsLink>
        </div>
      );
    }
    return (
      <Alert variant="destructive">
        <p className="font-medium">{message}</p>
        <p className={cn(PROSE, "text-sm")}>
          <CapabilityEvaluatorsLink
            capabilityId={capabilityId}
            className="underline"
            projectId={projectId}
          >
            Open the capability&apos;s Evaluations tab
          </CapabilityEvaluatorsLink>{" "}
          to add graders from the library or create a new set.
        </p>
      </Alert>
    );
  }

  if (status === "empty") {
    const copy = "No evaluators derived from the capability card after the code scan.";
    if (variant === "compact") {
      return (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
          {copy}{" "}
          <CapabilityEvaluatorsLink
            capabilityId={capabilityId}
            className="font-medium underline"
            projectId={projectId}
          >
            Create a set on Evaluations
          </CapabilityEvaluatorsLink>
        </div>
      );
    }
    return (
      <EmptyState
        action={
          <Button asChild size="sm" variant="secondary">
            <CapabilityEvaluatorsLink capabilityId={capabilityId} projectId={projectId}>
              <Icon.externalLink />
              Open Evaluations tab
            </CapabilityEvaluatorsLink>
          </Button>
        }
        className="rounded-md border border-dashed border-border"
        description={copy}
        icon={Icon.listBox}
        size="section"
        title="No evaluators could be authored"
      />
    );
  }

  if (variant === "compact") {
    return (
      <div className="rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
        No eval set yet — after code scan, author one on the capability&apos;s{" "}
        <CapabilityEvaluatorsLink
          capabilityId={capabilityId}
          className="font-medium underline"
          projectId={projectId}
        >
          Evaluations tab
        </CapabilityEvaluatorsLink>
        .
      </div>
    );
  }

  return (
    <EmptyState
      action={
        <Button asChild size="sm" variant="secondary">
          <CapabilityEvaluatorsLink capabilityId={capabilityId} projectId={projectId}>
            <Icon.externalLink />
            Open Evaluations tab
          </CapabilityEvaluatorsLink>
        </Button>
      }
      className="rounded-md border border-dashed border-border"
      description="After code scan, create an eval set on the capability's Evaluations tab — the active set grades optimiser candidates."
      icon={Icon.listBox}
      size="section"
      title="No eval set yet"
    />
  );
}
