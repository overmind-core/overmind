import { createContext, lazy, Suspense, useContext, useMemo, useState } from "react";

import {
  ActionBarPrimitive,
  type ExternalStoreAdapter,
  MessagePrimitive,
  makeAssistantDataUI,
  type ThreadMessageLike,
  ThreadPrimitive,
  type ToolCallMessagePartProps,
  useAuiState,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { StreamdownTextPrimitive } from "@assistant-ui/react-streamdown";

import {
  ToolFallbackArgs,
  ToolFallbackContent,
  ToolFallbackRoot,
} from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { Button } from "@/components/ui/button";
import { CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { workshopCode } from "@/lib/code-highlighting";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Cell, WorkshopCellReference, WorkshopInteraction, WorkshopMessage } from "@/openapi";

const WorkshopPresentationView = lazy(() => import("./workshop-presentation"));
const PresentationActions = createContext<{
  messages: WorkshopMessage[];
  onCell: (cell: WorkshopCellReference) => void;
  onSend: (text: string, interaction?: WorkshopInteraction) => Promise<void>;
}>({ messages: [], onCell: () => {}, onSend: async () => {} });

const WorkshopResultUI = makeAssistantDataUI<WorkshopMessage>({
  name: "workshop-result",
  render: function WorkshopResult({ data }) {
    const actions = useContext(PresentationActions);
    return (
      <Suspense fallback={<p className="text-xs text-muted-foreground">Loading result…</p>}>
        <WorkshopPresentationView message={data} {...actions} />
      </Suspense>
    );
  },
});

const CellContext = createContext<{ cells: Cell[]; onSelect: (id: string) => void }>({
  cells: [],
  onSelect: () => {},
});
const ActiveTaskContext = createContext<string | undefined>(undefined);

type ConversationMessage = WorkshopMessage & {
  steps?: WorkshopMessage[];
  entries?: WorkshopMessage[];
};

const activityLabels: Record<string, string> = {
  completed: "Done",
  failed: "Failed",
  interrupted: "Interrupted",
  preparing: "Preparing",
  running: "Running",
};

function ToolCalls({
  children,
  startIndex,
  endIndex,
}: React.PropsWithChildren<{ startIndex: number; endIndex: number }>) {
  const activeTaskId = useContext(ActiveTaskContext);
  const message = useAuiState(
    (state) => state.message.metadata.custom.workshop
  ) as ConversationMessage;
  const steps = (message.entries ?? [])
    .slice(startIndex, endIndex + 1)
    .filter((item) => item.role === "activity");
  const [expanded, setExpanded] = useState<boolean | undefined>();
  const running = steps.find((step) => step.status === "running" || step.status === "preparing");
  const failed = steps.some((step) => step.status === "failed");
  const status = running
    ? activityLabels[running.status]
    : failed
      ? "Failed"
      : steps.some((step) => step.status === "interrupted")
        ? "Interrupted"
        : "Done";
  const count = endIndex - startIndex + 1;
  return (
    <ToolGroupRoot
      onOpenChange={setExpanded}
      open={
        expanded ??
        !!(running || failed || steps.some((step) => step.id.startsWith(`${activeTaskId}:`)))
      }
      variant="ghost"
    >
      <ToolGroupTrigger
        active={!!running}
        aria-label={`${count} tool ${count === 1 ? "call" : "calls"} ${status}`}
        count={count}
      />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  );
}

function WorkshopToolCall({ toolCallId, toolName }: ToolCallMessagePartProps) {
  const message = useAuiState(
    (state) => state.message.metadata.custom.workshop
  ) as ConversationMessage;
  const step = message.steps?.find((item) => item.id === toolCallId);
  if (!step) return null;
  const hasDetails = !!(step.code?.trim() || step.error?.trim());
  const Trigger = hasDetails ? CollapsibleTrigger : "div";
  const python = toolName === "python" || toolName === "execute_python";
  const ToolIcon = python ? Icon.python : toolName === "present" ? Icon.chart : Icon.terminal;
  return (
    <ToolFallbackRoot>
      <Trigger className="group/tool flex w-full min-w-0 items-center gap-2 py-1.5 text-left text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex shrink-0">
              <ToolIcon className={cn("size-4 shrink-0", toolName === "present" && "text-cat-4")} />
            </span>
          </TooltipTrigger>
          <TooltipContent>{python ? "Python" : toolName}</TooltipContent>
        </Tooltip>
        <span className={python ? "sr-only" : "workshop-tool-name shrink-0 font-mono"}>
          {toolName}
        </span>
        <span className="min-w-0 truncate">{step.text}</span>
        <span className={cn("sr-only", step.status === "failed" && "not-sr-only text-error")}>
          {activityLabels[step.status] || step.status}
        </span>
        {hasDetails && (
          <Icon.chevronRight className="size-3 shrink-0 transition-transform group-data-[state=open]/tool:rotate-90" />
        )}
      </Trigger>
      {hasDetails && (
        <ToolFallbackContent>
          <ToolFallbackArgs
            argsText={step.code}
            language={
              python
                ? "python"
                : toolName === "sql"
                  ? "sql"
                  : toolName === "shell"
                    ? "bash"
                    : "text"
            }
          />
          {step.error && <p className="break-words text-xs text-error">{step.error}</p>}
        </ToolFallbackContent>
      )}
    </ToolFallbackRoot>
  );
}

function Text() {
  const { cells, onSelect } = useContext(CellContext);
  return (
    <StreamdownTextPrimitive
      className={`${PROSE} workshop-message-text text-sm leading-relaxed`}
      components={{
        a: ({ href, children }) => {
          const id = href?.startsWith("#cell=") ? href.slice(6) : null;
          if (id)
            return cells.some((cell) => cell.id === id) ? (
              <Button
                className="inline-flex align-baseline text-xs"
                onClick={() => onSelect(id)}
                size="xs"
                variant="secondary"
              >
                {children}
              </Button>
            ) : (
              <span>{children}</span>
            );
          return (
            <a
              className="text-primary underline-offset-4 hover:underline"
              href={href}
              rel="noopener noreferrer"
              target="_blank"
            >
              {children}
            </a>
          );
        },
        img: ({ alt }) => <span>{alt}</span>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
      }}
      controls={false}
      lineNumbers={false}
      plugins={{ code: workshopCode }}
      preprocess={(text) => text.replace(/\]\(cell:([a-zA-Z0-9-]+)\)/g, "](#cell=$1)")}
      security={{
        allowDataImages: false,
        allowedImagePrefixes: [],
        allowedProtocols: ["https", "http", "cell"],
      }}
    />
  );
}

function Message() {
  const nativeMessage = useAuiState((state) => state.message);
  const message = nativeMessage.metadata.custom.workshop as ConversationMessage | undefined;
  if (!message)
    return (
      <MessagePrimitive.Root>
        <MessagePrimitive.Parts components={{ Text }} />
      </MessagePrimitive.Root>
    );
  return <WorkshopMessageContent message={message} />;
}

function WorkshopMessageContent({ message }: { message: ConversationMessage }) {
  const { cells, onSelect } = useContext(CellContext);
  const user = message.role === "user";
  return (
    <MessagePrimitive.Root
      aria-label={user ? "Your message" : "Overmind response"}
      className={cn("workshop-message min-w-0", user && "workshop-message-user")}
      data-role={message.role}
    >
      <div className={user ? "workshop-user-prompt" : "flex min-w-0 flex-col gap-4"}>
        <MessagePrimitive.Parts
          components={{ Text, ToolGroup: ToolCalls, tools: { Fallback: WorkshopToolCall } }}
        />
      </div>
      {!message.presentation && !!message.cells?.length && (
        <div className={cn("mt-1 flex flex-wrap gap-x-3 gap-y-1", user && "justify-end")}>
          {message.cells.map((ref) => {
            const cell = cells.find((value) => value.id === ref.id);
            return (
              <Tooltip key={ref.id}>
                <TooltipTrigger asChild>
                  <Button
                    aria-label={`Show ${ref.name || cell?.title || "dataset"} cell`}
                    className={cn(
                      "max-w-full justify-start",
                      user ? "text-muted-foreground" : "text-primary"
                    )}
                    disabled={!cell}
                    onClick={() => onSelect(ref.id)}
                    size="xs"
                    variant="secondary"
                  >
                    <Icon.dataset className="size-3 text-cat-2" />
                    <span className="truncate">{ref.name || cell?.title || "Dataset cell"}</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {ref.rows.toLocaleString()} rows
                  {ref.columns ? ` · ${ref.columns.length} columns` : ""}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      )}
      {!user && message.entries?.some((entry) => entry.role === "assistant" && entry.text) && (
        <ActionBarPrimitive.Root
          className="mt-2 flex items-center gap-1 text-muted-foreground"
          hideWhenRunning
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <ActionBarPrimitive.Copy asChild>
                <Button aria-label="Copy response" size="icon-xs" variant="ghost">
                  <Icon.copy />
                </Button>
              </ActionBarPrimitive.Copy>
            </TooltipTrigger>
            <TooltipContent>Copy response</TooltipContent>
          </Tooltip>
        </ActionBarPrimitive.Root>
      )}
      {message.status === "pending" && (
        <p className="mt-1 text-xs text-muted-foreground">Queued for the next action</p>
      )}
      {message.status === "cancelled" && (
        <p className="mt-1 text-xs text-muted-foreground">Not delivered · task cancelled</p>
      )}
      {message.status === "interrupted" && !message.steps && (
        <p className="mt-1 text-xs text-warning">Response interrupted</p>
      )}
    </MessagePrimitive.Root>
  );
}

function convertMessage(message: ConversationMessage): ThreadMessageLike {
  return {
    content: (message.entries ?? [message]).map((entry) => {
      if (entry.role === "activity")
        return {
          args: {},
          argsText: entry.code || "",
          toolCallId: entry.id,
          toolName: entry.tool || "action",
          type: "tool-call" as const,
          ...(entry.status === "running" || entry.status === "preparing"
            ? {}
            : {
                isError: entry.status === "failed",
                result: entry.error || activityLabels[entry.status] || entry.status,
              }),
        };
      if (entry.presentation)
        return { data: entry, name: "workshop-result", type: "data" as const };
      return { text: entry.text, type: "text" as const };
    }),
    id: message.id,
    metadata: { custom: { workshop: message } },
    role: message.role === "user" ? "user" : "assistant",
    ...(message.role === "user"
      ? {}
      : {
          status:
            message.status === "streaming" ||
            message.status === "running" ||
            message.status === "preparing" ||
            message.steps?.some((step) => step.status === "running" || step.status === "preparing")
              ? { type: "running" as const }
              : { reason: "stop" as const, type: "complete" as const },
        }),
  };
}

export function useWorkshopRuntime(
  messages: WorkshopMessage[],
  adapter: Omit<ExternalStoreAdapter<ConversationMessage>, "messages" | "convertMessage">
) {
  const grouped = useMemo(() => {
    const result: ConversationMessage[] = [];
    for (const message of messages) {
      if (
        !(message.text || message.cells?.length || message.presentation) ||
        ["pending", "delivering", "removed"].includes(message.status)
      )
        continue;
      const previous = result.at(-1);
      if (
        message.role !== "user" &&
        previous?.role !== "user" &&
        previous?.entries &&
        previous.id.split(":")[0] === message.id.split(":")[0]
      ) {
        previous.entries!.push(message);
        if (message.role === "activity") previous.steps!.push(message);
        previous.status = message.status;
        previous.cells = [
          ...new Map(
            [...(previous.cells ?? []), ...(message.cells ?? [])].map((cell) => [cell.id, cell])
          ).values(),
        ];
      } else {
        result.push({
          ...message,
          entries: [message],
          steps: message.role === "activity" ? [message] : [],
        });
      }
    }
    return result;
  }, [messages]);
  return useExternalStoreRuntime({ ...adapter, convertMessage, messages: grouped });
}

export function WorkshopConversation({
  activity,
  activeTaskId,
  messages,
  cells,
  onSelect,
  onSend,
  onCell,
}: {
  activity?: string;
  activeTaskId?: string;
  messages: WorkshopMessage[];
  cells: Cell[];
  onSelect: (id: string) => void;
  onSend: (text: string, interaction?: WorkshopInteraction) => Promise<void>;
  onCell: (cell: WorkshopCellReference) => void;
}) {
  return (
    <ActiveTaskContext.Provider value={activeTaskId}>
      <CellContext.Provider value={{ cells, onSelect }}>
        <PresentationActions.Provider value={{ messages, onCell, onSend }}>
          <WorkshopResultUI />
          <div className="workshop-history relative flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport
              autoScroll
              className="workshop-conversation min-h-0 flex-1 overflow-y-auto overscroll-contain"
            >
              <ThreadPrimitive.Messages components={{ Message }} />
              {activity && (
                <div
                  aria-live="polite"
                  className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground"
                  role="status"
                >
                  <Spinner size="sm" />
                  <span>{activity}</span>
                </div>
              )}
              <ThreadPrimitive.Empty>
                <p className="py-2 text-xs text-muted-foreground">
                  Conversation history will appear here.
                </p>
              </ThreadPrimitive.Empty>
            </ThreadPrimitive.Viewport>
            <ThreadPrimitive.ScrollToBottom asChild>
              <Button
                aria-label="Scroll to latest message"
                className="absolute bottom-2 right-3 z-10 rounded-md border border-border bg-popover"
                size="icon-xs"
                variant="ghost"
              >
                <Icon.arrowDown />
              </Button>
            </ThreadPrimitive.ScrollToBottom>
          </div>
        </PresentationActions.Provider>
      </CellContext.Provider>
    </ActiveTaskContext.Provider>
  );
}
