import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import { Chart as BarChart3 } from "pixelarticons/react";

import type { PromptVersionOut } from "@/api";
import { PromptDiff } from "@/components/agent-detail/PromptDiff";
import { MiniStat } from "@/components/mini-stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";

type ViewMode = "full" | "diff";

export function VersionsTab({
  agentName,
  projectId,
  versions,
}: {
  agentName: string;
  projectId: string;
  versions: PromptVersionOut[];
}) {
  // versions are passed sorted descending (latest first); reverse for diff lookup
  const [expanded, setExpanded] = useState<number | null>(versions[0]?.version ?? null);
  const [viewMode, setViewMode] = useState<Record<number, ViewMode>>(() => {
    // Default all versions (except the first/oldest) to diff view
    const initial: Record<number, ViewMode> = {};
    for (let i = 0; i < versions.length - 1; i++) {
      initial[versions[i].version] = "diff";
    }
    return initial;
  });
  const navigate = useNavigate();

  // Build a map of version → previous prompt text for diff
  const prevTextByVersion = new Map<number, string>();
  for (let i = 0; i < versions.length - 1; i++) {
    // versions[i] is newer, versions[i+1] is older
    prevTextByVersion.set(versions[i].version, versions[i + 1].promptText ?? "");
  }

  if (versions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center border border-dashed border-border py-16">
        <BarChart3 className="mb-3 size-12 text-muted-foreground/50" />
        <p className="text-sm italic text-muted-foreground">No prompt versions found.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {versions.map((v) => {
        const isExpanded = expanded === v.version;
        const mode = viewMode[v.version] ?? "full";
        const prevText = prevTextByVersion.get(v.version);
        const hasPrev = prevText !== undefined;

        return (
          <div className="overflow-hidden border border-border bg-card" key={v.version}>
            <div className="flex flex-row flex-wrap items-center justify-between gap-4 border-b border-border bg-muted/20 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-bold capitalize">{agentName}</span>
                    <Badge variant="outline">v{v.version}</Badge>
                    <Badge variant="secondary">{v.slug}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {formatDate(v.createdAt ?? "")}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isExpanded && hasPrev && (
                  <div className="flex overflow-hidden rounded-md border border-border text-xs">
                    <button
                      className={`px-2.5 py-1 transition-colors ${
                        mode === "full"
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:bg-muted/60"
                      }`}
                      onClick={() => setViewMode((prev) => ({ ...prev, [v.version]: "full" }))}
                      type="button"
                    >
                      Full
                    </button>
                    <button
                      className={`px-2.5 py-1 transition-colors ${
                        mode === "diff"
                          ? "bg-accent text-accent-foreground"
                          : "text-muted-foreground hover:bg-muted/60"
                      }`}
                      onClick={() => setViewMode((prev) => ({ ...prev, [v.version]: "diff" }))}
                      type="button"
                    >
                      Diff
                    </button>
                  </div>
                )}
                <Button
                  onClick={() => setExpanded(isExpanded ? null : v.version)}
                  size="sm"
                  variant="outline"
                >
                  {isExpanded ? "Hide" : "Show"} template
                </Button>
              </div>
            </div>
            <div className="p-4">
              <div className="mb-4 flex flex-wrap gap-4">
                <MiniStat label="Spans" value={v.totalSpans?.toLocaleString() ?? "—"} />
                <MiniStat
                  label="Scored"
                  value={`${v.scoredSpans?.toLocaleString() ?? "—"} / ${v.totalSpans?.toLocaleString() ?? "—"}`}
                />
                <MiniStat
                  label="Avg Score"
                  value={v.avgScore != null ? `${(v.avgScore * 100).toFixed(1)}%` : "—"}
                />
                <MiniStat
                  label="Avg Latency"
                  value={v.avgLatencyMs != null ? `${v.avgLatencyMs.toFixed(0)} ms` : "—"}
                />
                <MiniStat label="Hash" value={`${v.hash.slice(0, 10)}…`} />
              </div>
              <div className="mb-4 flex flex-wrap gap-2">
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="outline"
                >
                  View spans
                </Button>
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId, sortBy: "judgeScore" },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="ghost"
                >
                  Best by judge
                </Button>
                <Button
                  onClick={() =>
                    navigate({
                      params: { projectId },
                      search: { agent: v.promptId, sortBy: "duration" },
                      to: "/projects/$projectId/traces",
                    })
                  }
                  size="sm"
                  variant="ghost"
                >
                  Slowest
                </Button>
              </div>
              {isExpanded &&
                (mode === "diff" && hasPrev ? (
                  <PromptDiff newText={v.promptText ?? ""} oldText={prevText} />
                ) : (
                  <pre className="max-h-[260px] overflow-y-auto border border-border bg-muted/30 p-4 font-mono text-xs whitespace-pre-wrap">
                    {v.promptText ?? ""}
                  </pre>
                ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
