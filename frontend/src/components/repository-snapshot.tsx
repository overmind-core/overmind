import { formatDistanceToNowStrict } from "date-fns";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import type { AgentGraph } from "@/openapi";

export function RepositorySnapshot({ graph }: { graph: AgentGraph }) {
  const snapshot = graph.repositorySnapshot;
  const hasScan = !!graph.lastSyncedAt || graph.capabilities.length > 0;

  return (
    <section aria-label="Repository snapshot" className="max-w-full space-y-1.5 sm:text-right">
      <p className="text-xs text-muted-foreground">Repository snapshot</p>
      {snapshot ? (
        <Badge className="max-w-full overflow-hidden px-0" size="chip" variant="neutral">
          <span
            className="inline-flex h-full min-w-0 max-w-64 items-center gap-1.5 px-2"
            title={snapshot.repository}
          >
            <Icon.folder className="size-3 shrink-0" />
            <span className="truncate">{snapshot.repository}</span>
          </span>
          <span
            className="inline-flex h-full min-w-0 max-w-40 items-center gap-1.5 border-l border-border/70 px-2"
            title={snapshot.branch || "Detached HEAD"}
          >
            <Icon.gitBranch className="size-3 shrink-0" />
            <span className="truncate font-mono">{snapshot.branch || "Detached HEAD"}</span>
          </span>
          <span
            className="inline-flex h-full shrink-0 items-center gap-1.5 border-l border-border/70 px-2"
            title={snapshot.scannedAt.toLocaleString()}
          >
            <Icon.history className="size-3" />
            Scanned {formatDistanceToNowStrict(snapshot.scannedAt, { addSuffix: true })}
          </span>
        </Badge>
      ) : (
        <Badge
          size="chip"
          title="Run /overmind setup in your coding agent from the repository to update the snapshot."
          variant="neutral"
        >
          {hasScan ? "Revision unavailable" : "No repository snapshot"}
        </Badge>
      )}
    </section>
  );
}
