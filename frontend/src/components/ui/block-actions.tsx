import { useState } from "react";

import { Check, Clipboard, Eye, EyeOff } from "pixelarticons/react";

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
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={onToggleMode}
          title={mode === "raw" ? "Render markdown" : "View raw"}
          type="button"
        >
          {mode === "raw" ? (
            <>
              <Eye className="size-3" />
              Preview
            </>
          ) : (
            <>
              <EyeOff className="size-3" />
              Raw
            </>
          )}
        </button>
      )}
      <button
        className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        onClick={copy}
        title={copied ? "Copied!" : "Copy to clipboard"}
        type="button"
      >
        {copied ? <Check className="size-3 text-emerald-500" /> : <Clipboard className="size-3" />}
      </button>
    </div>
  );
}
