import { Alert } from "@/components/ui/alert";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { TraceErrorDetails } from "@/openapi";

export function TraceErrorDetailsView({ details }: { details: TraceErrorDetails }) {
  const types = [...new Set(details.records.map((record) => record.type).filter(Boolean))];
  const messages = [
    ...new Set(details.records.map((record) => record.message).filter(Boolean)),
  ].map((message) => {
    const type = details.records.find((record) => record.message === message && record.type)?.type;
    return type && !message.startsWith(type) ? `${type}: ${message}` : message;
  });
  const stacks = [...new Set(details.records.map((record) => record.stacktrace).filter(Boolean))];
  const text = [
    ...types,
    ...Object.entries(details.context).map(([key, value]) => `${key}: ${value}`),
    ...messages,
    ...stacks,
  ].join("\n\n");
  const { copied, copy } = useCopy(text || "No error message captured");
  return (
    <section aria-label="Error details" className="max-h-[40vh] shrink-0 overflow-auto px-3 py-1">
      <Alert className="gap-2 border-0 bg-destructive/5 px-3 py-2 text-xs" variant="destructive">
        <span className="flex size-5 shrink-0 items-center justify-center rounded-sm bg-destructive/10">
          <Icon.warning className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          {messages.length === 0 && (
            <>
              {details.summary !== "No error message captured" && (
                <p className="whitespace-pre-wrap break-words font-mono text-xs leading-5">
                  {details.summary}
                </p>
              )}
              <p className="text-xs">No error message captured</p>
            </>
          )}
          {messages.map((message) =>
            message.length > 300 ? (
              <details className="group font-mono text-xs leading-5" key={message}>
                <summary className="cursor-pointer break-words">
                  <span className="group-open:hidden">{message.slice(0, 300)}… </span>
                  <span className="underline underline-offset-4">Full message</span>
                </summary>
                <p className="mt-2 whitespace-pre-wrap break-words">{message}</p>
              </details>
            ) : (
              <p
                className="whitespace-pre-wrap break-words font-mono text-xs leading-5"
                key={message}
              >
                {message}
              </p>
            )
          )}
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            {(Object.keys(details.context).length > 0 || types.length > 0) && (
              <details className="text-xs text-foreground open:w-full">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  Technical details
                </summary>
                <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                  {types.length > 0 && (
                    <>
                      <dt className="text-muted-foreground">Error type</dt>
                      <dd className="break-words font-mono">{types.join(" · ")}</dd>
                    </>
                  )}
                  {Object.entries(details.context).map(([key, value]) => (
                    <div className="contents" key={key}>
                      <dt className="break-all text-muted-foreground">{key}</dt>
                      <dd className="whitespace-pre-wrap break-all font-mono">{value}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            {stacks.map((stack, index) => (
              <details className="text-xs text-foreground open:w-full" key={stack}>
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  Stack trace{stacks.length > 1 ? ` ${index + 1}` : ""}
                </summary>
                <pre className="mt-2 whitespace-pre-wrap break-words font-mono">{stack}</pre>
              </details>
            ))}
          </div>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button aria-label="Copy error details" onClick={copy} size="icon-sm" variant="ghost">
              {copied ? <Icon.success /> : <Icon.clipboard />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{copied ? "Copied" : "Copy error details"}</TooltipContent>
        </Tooltip>
      </Alert>
    </section>
  );
}
