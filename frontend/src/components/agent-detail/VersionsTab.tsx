import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Chart as BarChart3 } from "pixelarticons/react";

import type { PromptVersionOut } from "@/api";
import { MiniStat } from "@/components/mini-stat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";

export function VersionsTab({
  agentName,
  projectId,
  versions,
}: {
  agentName: string;
  projectId: string;
  versions: PromptVersionOut[];
}) {
  const [expanded, setExpanded] = useState<number | null>(versions[0]?.version ?? null);
  const navigate = useNavigate();

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
              <Button
                onClick={() => setExpanded(isExpanded ? null : v.version)}
                size="sm"
                variant="outline"
              >
                {isExpanded ? "Hide" : "Show"} template
              </Button>
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
              {isExpanded && (
                <pre className="max-h-[260px] overflow-y-auto border border-border bg-muted/30 p-4 font-mono text-xs whitespace-pre-wrap">
                  {v.promptText ?? ""}
                </pre>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
