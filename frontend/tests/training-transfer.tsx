import { useState } from "react";
import { createRoot } from "react-dom/client";

import { FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import { StageProgress } from "@/components/finetuning/stage-progress";
import { Button } from "@/components/ui/button";
import { type FinetuningProgress, stageDetailLine, stageLabel } from "@/lib/finetuning-progress";

import "../src/styles.css";

const now = Date.now() / 1000;
const fixtures: Record<string, FinetuningProgress> = {
  Adapters: {
    diagnostics: {
      heartbeat_at: now - 5,
      last_progress_at: now - 25,
      stage: "configuring_adapters",
      stage_started_at: now - 25,
    },
    stage: "configuring_adapters",
  },
  Loading: {
    diagnostics: {
      heartbeat_at: now - 5,
      last_progress_at: now - 130,
      stage: "loading_model",
      stage_started_at: now - 130,
    },
    stage: "loading_model",
  },
  "Loading without telemetry": { stage: "loading_model" },
  Selecting: {
    diagnostics: {
      completed: 56000,
      last_progress_at: now - 3,
      source_at: now - 3,
      stage: "selecting_prepared_rows",
      stage_started_at: now - 70,
      total: 224000,
      unit: "rows",
    },
    stage: "transferring",
  },
  Unavailable: { stage: "transferring" },
  Uploading: {
    diagnostics: {
      completed: 32768,
      files_completed: 1,
      files_total: 2,
      last_progress_at: now - 5,
      measurement: "provider_acknowledged_files",
      stage: "uploading_selections",
      stage_started_at: now - 20,
      total: 65536,
      unit: "bytes",
    },
    stage: "transferring",
  },
};

function Preview() {
  const [selected, setSelected] = useState("Selecting");
  const progress = fixtures[selected];
  return (
    <main className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <h1 className="text-xl">Training startup progress · UI fixture</h1>
      <p className="text-sm text-muted-foreground">
        Synthetic measurements. No job or provider call.
      </p>
      <div className="flex flex-wrap gap-2">
        {Object.keys(fixtures).map((name) => (
          <Button key={name} onClick={() => setSelected(name)} variant="outline">
            {name}
          </Button>
        ))}
      </div>
      <section className="flex flex-col gap-3 rounded-md border border-border p-4">
        <div className="flex flex-wrap items-center gap-2.5">
          <FtStatusBadge
            fallback="queued"
            label={stageLabel(progress) ?? "Transferring data"}
            solidProgress
            status="preparing"
          />
          <p className="min-w-0 flex-1 font-mono text-xs text-muted-foreground">
            {stageDetailLine(progress)}
          </p>
        </div>
        <StageProgress progress={progress} />
      </section>
    </main>
  );
}

const root = document.getElementById("app");
if (root) createRoot(root).render(<Preview />);
