import type { ReactNode } from "react";

import { DialogBody } from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";

export function DatapointInspector({ input, children }: { input: ReactNode; children: ReactNode }) {
  return (
    <DialogBody className="flex min-h-0 flex-1 flex-col p-0">
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-5 lg:overflow-hidden">
        <section
          aria-label="Evaluation input"
          className="flex min-w-0 flex-col border-b border-border lg:col-span-2 lg:min-h-0 lg:border-r lg:border-b-0"
        >
          <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-5">
            <Icon.dataset className="size-3.5 text-muted-foreground" />
            <h3 className="text-xs font-medium text-muted-foreground">Input</h3>
          </div>
          <div className="divide-y divide-border/70 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            {input}
          </div>
        </section>
        <section
          aria-label="Output and evaluation"
          className="flex min-w-0 flex-col lg:col-span-3 lg:min-h-0"
        >
          <div className="flex h-10 shrink-0 items-center border-b border-border px-5">
            <h3 className="text-xs font-medium text-muted-foreground">Output and evaluation</h3>
          </div>
          <div
            aria-label="Model outputs"
            className="divide-y divide-border outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset lg:min-h-0 lg:flex-1 lg:overflow-y-auto"
            role="region"
            // biome-ignore lint/a11y/noNoninteractiveTabindex: Model results need an independently keyboard-scrollable region.
            tabIndex={0}
          >
            {children}
          </div>
        </section>
      </div>
    </DialogBody>
  );
}
