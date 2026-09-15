import type { ReactNode } from "react";

import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export type EntityKind =
  | "capability"
  | "dataset"
  | "evalRun"
  | "experiment"
  | "job"
  | "model"
  | "project"
  | "trace";

// Deliberately the sidebar's icon for each vertical, so a chip says where the
// entity lives before you read its name.
export const ENTITY_META: Record<
  EntityKind,
  { icon: (typeof Icon)[keyof typeof Icon]; label: string }
> = {
  capability: { icon: Icon.agent, label: "Capability" },
  dataset: { icon: Icon.dataset, label: "Dataset" },
  evalRun: { icon: Icon.eval, label: "Eval run" },
  experiment: { icon: Icon.optimiser, label: "Optimisation" },
  job: { icon: Icon.training, label: "Training run" },
  model: { icon: Icon.inference, label: "Model" },
  project: { icon: Icon.project, label: "Project" },
  trace: { icon: Icon.observability, label: "Trace" },
};

export function Row({ children, label }: { children: ReactNode; label: string }) {
  if (children == null || children === "") return null;
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate text-right text-xs text-foreground">{children}</span>
    </div>
  );
}

export function EntityCardShell({
  children,
  footer,
  kind,
  name,
  subtitle,
}: {
  children?: ReactNode;
  footer?: ReactNode;
  kind: EntityKind;
  name: ReactNode;
  subtitle?: ReactNode;
}) {
  const meta = ENTITY_META[kind];
  return (
    <div className="divide-y divide-border/70">
      <div className="flex items-start gap-2 px-3 py-2.5">
        <meta.icon aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{name}</p>
          <p className="chip-label mt-0.5 text-xs text-muted-foreground">
            {meta.label}
            {subtitle ? <span className="normal-case"> · {subtitle}</span> : null}
          </p>
        </div>
      </div>
      {children ? <div className="space-y-1.5 px-3 py-2.5">{children}</div> : null}
      {footer ? (
        <div className="flex items-center justify-between gap-2 px-3 py-2">{footer}</div>
      ) : null}
    </div>
  );
}

export function EntityCardSkeleton({ kind, name }: { kind: EntityKind; name: ReactNode }) {
  return (
    <EntityCardShell kind={kind} name={name}>
      {[0, 1, 2].map((i) => (
        <div className="flex items-baseline justify-between gap-3" key={i}>
          <Skeleton className="h-3 w-16" />
          <Skeleton className={cn("h-3", i === 1 ? "w-28" : "w-20")} />
        </div>
      ))}
    </EntityCardShell>
  );
}

export function EntityCardMissing({ kind, name }: { kind: EntityKind; name: ReactNode }) {
  return (
    <EntityCardShell kind={kind} name={name}>
      <p className="text-xs text-muted-foreground">
        No details available — it may have been deleted, or belong to another project.
      </p>
    </EntityCardShell>
  );
}
