import { useState } from "react";

import { Icon } from "@/components/ui/icons";

export function useCopy(text: string) {
  const [copied, setCopied] = useState(false);
  function copy() {
    void navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  return { copied, copy };
}

export function BlockActions({
  text,
  mode,
  onToggleMode,
  showToggle,
}: {
  text: string;
  mode: "raw" | "markdown";
  onToggleMode: () => void;
  showToggle: boolean;
}) {
  const { copied, copy } = useCopy(text);
  return (
    <div className="flex items-center gap-1">
      {showToggle && (
        <button
          className="flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          onClick={onToggleMode}
          title={mode === "raw" ? "Render markdown" : "View raw"}
          type="button"
        >
          {mode === "raw" ? (
            <>
              <Icon.view className="size-3" />
              Preview
            </>
          ) : (
            <>
              <Icon.hide className="size-3" />
              Raw
            </>
          )}
        </button>
      )}
      <button
        className="rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        onClick={copy}
        title={copied ? "Copied!" : "Copy to clipboard"}
        type="button"
      >
        {copied ? (
          <Icon.success className="size-3 text-success" />
        ) : (
          <Icon.clipboard className="size-3" />
        )}
      </button>
    </div>
  );
}
