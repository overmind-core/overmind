import type { EvalSampleIO } from "@/openapi";
import { renderPayload, type ViewMode } from "./payload-format";

type Message = {
  role: string;
  content?: unknown;
  name?: string;
  tool_calls?: unknown[];
  tool_call_id?: string;
};

function isMessages(value: unknown): value is Message[] {
  return (
    Array.isArray(value) &&
    value.every(
      (message) => message && typeof message === "object" && typeof message.role === "string"
    )
  );
}

function payload(value: unknown, mode: ViewMode) {
  return renderPayload(typeof value === "string" ? value : JSON.stringify(value), mode);
}

export function SampleInput({ io, viewMode }: { io?: EvalSampleIO; viewMode: ViewMode }) {
  const input: unknown = io?.input;
  const source = io?.inputSource ?? "unavailable";
  const messages = isMessages(input)
    ? input
    : input && typeof input === "object" && "messages" in input && isMessages(input.messages)
      ? input.messages
      : null;
  const tools = input && typeof input === "object" && "tools" in input ? input.tools : null;

  return (
    <section aria-label="Evaluation input" className="flex flex-col gap-4 px-5 py-4">
      <div className="space-y-1">
        <h3 className="text-sm font-medium">
          {source === "recorded" ? "Initial model input" : "Dataset input"}
        </h3>
        <p className="text-xs text-muted-foreground">
          {source === "recorded"
            ? "Captured at the model runner, including the system prompt and tools."
            : source === "dataset"
              ? "Pinned dataset version. The exact model request was not captured for this sample."
              : "Input was not captured and the pinned dataset row is unavailable."}
        </p>
        {io?.truncated && <p className="text-xs text-warning">Stored payload was truncated.</p>}
      </div>
      {input != null && (viewMode === "raw" || !messages) ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-xs">
          {payload(input, viewMode)}
        </pre>
      ) : (
        messages?.map((message, i) => (
          <div className="space-y-2" key={i}>
            <span className="text-xs font-medium capitalize text-muted-foreground">
              {message.role ?? "Message"}
              {message.name ? ` · ${message.name}` : ""}
            </span>
            {message.content != null && message.content !== "" && (
              <pre className="whitespace-pre-wrap break-words font-mono text-xs">
                {payload(message.content, viewMode)}
              </pre>
            )}
            {message.tool_calls && (
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Tool calls</p>
                <pre className="whitespace-pre-wrap break-words font-mono text-xs">
                  {payload(message.tool_calls, viewMode)}
                </pre>
              </div>
            )}
            {message.tool_call_id && (
              <p className="break-all font-mono text-xs text-muted-foreground">
                {message.tool_call_id}
              </p>
            )}
          </div>
        ))
      )}
      {viewMode !== "raw" && Array.isArray(tools) && tools.length > 0 && (
        <details className="rounded-md border border-border p-3">
          <summary className="cursor-pointer text-xs">Tools · {tools.length}</summary>
          <pre className="mt-3 whitespace-pre-wrap break-words font-mono text-xs">
            {payload(tools, viewMode)}
          </pre>
        </details>
      )}
    </section>
  );
}
