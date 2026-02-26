import React, { useMemo, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ThumbsDown, ThumbsUp } from "lucide-react";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type { SpanRow } from "@/hooks/use-traces";
import { spanStatusLabel } from "@/hooks/use-traces";

function formatAttributeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") {
    if ("judge_feedback" in value || "agent_feedback" in value) {
      const { judge_feedback: _j, agent_feedback: _a, ...rest } = value as Record<string, unknown>;
      return JSON.stringify(rest, null, 2);
    }
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function AttributesTable({
  title,
  attributes,
}: {
  title: string;
  attributes: Record<string, unknown> | undefined;
}) {
  const entries = attributes && typeof attributes === "object" ? Object.entries(attributes) : [];
  if (entries.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="border-b border-border bg-muted/50 px-3 py-2 text-xs font-semibold uppercase text-muted-foreground">
        {title}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[35%] font-medium">Key</TableHead>
            <TableHead className="font-medium">Value</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map(([key, value]) => (
            <TableRow key={key}>
              <TableCell className="align-top font-mono text-xs text-muted-foreground">
                {/* TODO: migrate to say agent_id */}
                {key === "PromptId" ? "AgentId" : key}
              </TableCell>
              <TableCell className="text-xs">
                <pre className="wrap-break-word whitespace-pre-wrap rounded bg-muted/30 px-2 py-1.5 font-mono text-xs">
                  {formatAttributeValue(value)}
                </pre>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

type ToolCallItem = {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
};

type ToolParameterSchema = {
  type?: string;
  description?: string;
  enum?: unknown[];
  [key: string]: unknown;
};

type ToolFunctionDef = {
  name?: string;
  description?: string;
  parameters?: {
    type?: string;
    properties?: Record<string, ToolParameterSchema>;
    required?: string[];
    [key: string]: unknown;
  };
};

type ToolDefinition = {
  type?: string;
  function?: ToolFunctionDef;
  // flat shape fallback (name at top level)
  name?: string;
  description?: string;
  parameters?: ToolFunctionDef["parameters"];
};

type ChatMessage = {
  role?: string;
  content?: string | null;
  tool_calls?: ToolCallItem[];
  tool_call_id?: string;
};

function extractToolNamesFromOutput(outputs: unknown): string[] {
  try {
    const parsed = typeof outputs === "string" ? JSON.parse(outputs) : outputs;
    const names: string[] = [];
    const collect = (toolCalls: unknown[]) => {
      for (const tc of toolCalls) {
        const name = (tc as ToolCallItem)?.function?.name;
        if (name) names.push(name);
      }
    };
    if (Array.isArray(parsed)) {
      for (const msg of parsed) {
        if (Array.isArray(msg?.tool_calls)) collect(msg.tool_calls);
      }
    } else if (Array.isArray((parsed as ChatMessage)?.tool_calls)) {
      collect((parsed as ChatMessage).tool_calls!);
    }
    return [...new Set(names)];
  } catch {
    return [];
  }
}

function ToolCallBadge() {
  return (
    <span className="inline-flex items-center rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:border-violet-700 dark:bg-violet-900/30 dark:text-violet-400">
      tool call
    </span>
  );
}

function ToolCallBlock({ tc }: { tc: ToolCallItem }) {
  const prettyArgs = useMemo(() => {
    if (!tc.function?.arguments) return "";
    try {
      return JSON.stringify(JSON.parse(tc.function.arguments), null, 2);
    } catch {
      return tc.function.arguments;
    }
  }, [tc.function?.arguments]);

  return (
    <div className="mt-1 rounded border border-border/60 bg-muted/40 px-2 py-1.5">
      <span className="font-semibold text-violet-600 dark:text-violet-400">
        {tc.function?.name ?? "tool"}
      </span>
      {prettyArgs && (
        <pre className="mt-1 whitespace-pre-wrap wrap-break-word text-muted-foreground">
          {prettyArgs}
        </pre>
      )}
    </div>
  );
}

function ToolParamRow({
  name,
  schema,
  required,
}: {
  name: string;
  schema: ToolParameterSchema;
  required: boolean;
}) {
  return (
    <div className="grid grid-cols-[140px_80px_60px_1fr] gap-x-3 items-baseline py-1.5 border-b border-border/40 last:border-0 text-xs">
      <span className="font-mono text-violet-500 dark:text-violet-400 truncate">{name}</span>
      <span className="font-mono text-muted-foreground">{schema.type ?? "—"}</span>
      <span>
        {required ? (
          <span className="text-amber-600 dark:text-amber-400 font-medium">required</span>
        ) : (
          <span className="text-muted-foreground/60">optional</span>
        )}
      </span>
      <span className="text-muted-foreground leading-relaxed">
        {schema.description ?? ""}
        {schema.enum && schema.enum.length > 0 && (
          <span className="ml-1 text-muted-foreground/70">
            ({schema.enum.map((v) => JSON.stringify(v)).join(" | ")})
          </span>
        )}
      </span>
    </div>
  );
}

function AvailableToolCard({ tool }: { tool: ToolDefinition }) {
  // Support both { type: "function", function: { name, ... } } and flat { name, ... }
  const fn = tool.function ?? tool;
  const name = fn.name;
  const description = fn.description;
  const parameters = fn.parameters;
  const params = parameters?.properties ?? {};
  const required = new Set(parameters?.required ?? []);
  const paramEntries = Object.entries(params);

  return (
    <div className="rounded border border-border/70 bg-muted/20 overflow-hidden">
      <div className="flex items-start gap-2 px-3 py-2 bg-muted/40 border-b border-border/50">
        <span className="font-semibold text-violet-600 dark:text-violet-400 font-mono text-sm leading-snug">
          {name ?? "unnamed"}
        </span>
      </div>
      {description && (
        <p className="px-3 py-2 text-xs text-muted-foreground border-b border-border/40 leading-relaxed">
          {description}
        </p>
      )}
      {paramEntries.length > 0 && (
        <div className="px-3 py-2">
          <div className="grid grid-cols-[140px_80px_60px_1fr] gap-x-3 mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/60">
            <span>Parameter</span>
            <span>Type</span>
            <span>Required</span>
            <span>Description</span>
          </div>
          {paramEntries.map(([pName, pSchema]) => (
            <ToolParamRow
              key={pName}
              name={pName}
              required={required.has(pName)}
              schema={pSchema}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function AvailableToolsBlock({ tools }: { tools: ToolDefinition[] }) {
  if (!tools || tools.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="border-b border-border bg-muted/50 px-3 py-2 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase text-muted-foreground">
          Available Tools
        </span>
        <span className="inline-flex items-center rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-700 dark:border-violet-700 dark:bg-violet-900/30 dark:text-violet-400">
          {tools.length}
        </span>
      </div>
      <div className="p-3 space-y-2">
        {tools.map((tool, idx) => (
          <AvailableToolCard key={idx} tool={tool} />
        ))}
      </div>
    </div>
  );
}

const ROLE_LABELS: Record<string, string> = {
  user: "User",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

function MessageRow({ msg }: { msg: ChatMessage }) {
  const roleLabel = ROLE_LABELS[msg.role ?? ""] ?? msg.role ?? "Message";
  const hasToolCalls = (msg.tool_calls?.length ?? 0) > 0;

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-semibold text-muted-foreground">{roleLabel}</span>
        {hasToolCalls && (
          <span className="inline-flex items-center rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:border-violet-700 dark:bg-violet-900/30 dark:text-violet-400">
            tool call
          </span>
        )}
      </div>
      {msg.content != null && msg.content !== "" && (
        <div className="whitespace-pre-wrap wrap-break-word">{msg.content}</div>
      )}
      {msg.tool_calls?.map((tc, tcIdx) => (
        <ToolCallBlock key={tcIdx} tc={tc} />
      ))}
    </div>
  );
}

function JsonBlock({
  title,
  value,
  badge,
}: {
  title: string;
  value: unknown;
  badge?: React.ReactNode;
}) {
  const parsedValue = useMemo(() => {
    try {
      return JSON.parse(value as string);
    } catch {
      return value;
    }
  }, [value]);

  const header = (
    <div className="border-b border-border bg-muted/50 px-3 py-2 flex items-center gap-2">
      <span className="text-xs font-semibold uppercase text-muted-foreground">{title}</span>
      {badge}
    </div>
  );

  if (typeof parsedValue === "string" && parsedValue.length > 0) {
    return (
      <div className="overflow-hidden rounded-md border border-border">
        {header}
        <div className="max-h-64 overflow-auto p-3 text-xs font-mono space-y-4">{parsedValue}</div>
      </div>
    );
  }
  if (!Array.isArray(parsedValue)) {
    if (parsedValue !== null && typeof parsedValue === "object") {
      return (
        <div className="overflow-hidden rounded-md border border-border">
          {header}
          <pre className="max-h-64 overflow-auto p-3 text-xs font-mono whitespace-pre-wrap wrap-break-word">
            {JSON.stringify(parsedValue, null, 2)}
          </pre>
        </div>
      );
    }
    return null;
  }
  if (parsedValue.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-md border border-border">
      {header}
      <div className="max-h-64 overflow-auto p-3 text-xs font-mono divide-y divide-border/40">
        {(parsedValue as ChatMessage[]).map((msg, idx) => (
          <div key={idx} className="py-3 first:pt-1">
            <MessageRow msg={msg} />
          </div>
        ))}
      </div>
    </div>
  );
}

function FeedbackTextInput({
  feedbackMutation,
  feedbackType,
  initialText,
}: {
  feedbackMutation: {
    mutate: (vars: {
      feedbackType: "judge" | "agent";
      rating: "up" | "down";
      text?: string;
    }) => void;
    isPending: boolean;
  };
  feedbackType: "judge" | "agent";
  initialText?: string | null;
}) {
  const [text, setText] = useState(initialText ?? "");
  return (
    <div className="flex gap-2">
      <Textarea
        className="min-h-[60px]"
        id="feedback-text"
        onChange={(e) => setText(e.target.value)}
        placeholder={`Add a note for ${feedbackType} feedback (optional)`}
        value={text}
      />
      <Button
        disabled={feedbackMutation.isPending || !text.trim()}
        onClick={() => {
          setText("");
          feedbackMutation.mutate({ feedbackType, rating: "up", text: text.trim() || undefined });
        }}
        size="sm"
      >
        Submit
      </Button>
    </div>
  );
}

interface SpanDetailViewProps {
  span: SpanRow;
  queryKey: unknown[];
}

export function SpanDetailView({ span, queryKey }: SpanDetailViewProps) {
  const queryClient = useQueryClient();
  const judgeFeedback = span.judgeScore;
  const agentFeedback = span.agentScore;
  const correctness =
    (span.feedbackScores?.correctness as number | undefined) ??
    ((span.spanAttributes?.feedback_score as Record<string, unknown> | undefined)?.correctness as
      | number
      | undefined);

  const feedbackMutation = useMutation({
    mutationFn: async ({
      feedbackType,
      rating,
      text,
    }: {
      feedbackType: "judge" | "agent";
      rating: "up" | "down";
      text?: string;
    }) => {
      return apiClient.spans.submitSpanFeedbackApiV1SpansSpanIdFeedbackPatch({
        spanFeedbackRequest: { feedbackType, rating, text },
        spanId: span.spanId,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  return (
    <Card>
      <CardHeader>
        <h2 className="text-base font-semibold">Span Details</h2>
        <p className="text-xs text-muted-foreground">
          {span.scopeName || span.name || "—"} · {span.spanId.slice(0, 8)}…
        </p>
      </CardHeader>
      <CardContent>
        <Tabs className="w-full" defaultValue="data">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="data">Data</TabsTrigger>
            <TabsTrigger value="feedback">Feedback</TabsTrigger>
            <TabsTrigger value="metadata">Metadata</TabsTrigger>
          </TabsList>

          <TabsContent className="space-y-4 mt-4" value="data">
            <JsonBlock title="Input" value={span.inputs} />
            <JsonBlock
              title="Output"
              value={span.outputs}
              badge={
                span.spanAttributes?.response_type === "tool_calls" ? <ToolCallBadge /> : undefined
              }
            />
            {Array.isArray(span.spanAttributes?.available_tools) &&
              (span.spanAttributes.available_tools as ToolDefinition[]).length > 0 && (
                <AvailableToolsBlock
                  tools={span.spanAttributes.available_tools as ToolDefinition[]}
                />
              )}
          </TabsContent>

          <TabsContent className="space-y-4 mt-4" value="feedback">
            <div className="rounded-md border border-border p-4 space-y-4">
              <div>
                <Label className="text-xs font-semibold text-muted-foreground">
                  Eval Score (Correctness)
                </Label>
                <p className="mt-1 text-2xl font-semibold">
                  {correctness != null ? (
                    <span>{(Number(correctness) * 100).toFixed(0)}%</span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </p>
              </div>
              <div className="space-y-3 pt-2 border-t border-border">
                <Label className="text-xs font-semibold">Your feedback</Label>
                <div className="flex flex-wrap gap-4">
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">Judge score</p>
                    <div className="flex gap-2">
                      <Button
                        disabled={feedbackMutation.isPending}
                        onClick={() =>
                          feedbackMutation.mutate({ feedbackType: "judge", rating: "up" })
                        }
                        size="sm"
                        variant={judgeFeedback?.rating === "up" ? "default" : "outline"}
                      >
                        <ThumbsUp className="size-4" />
                      </Button>
                      <Button
                        disabled={feedbackMutation.isPending}
                        onClick={() =>
                          feedbackMutation.mutate({ feedbackType: "judge", rating: "down" })
                        }
                        size="sm"
                        variant={judgeFeedback?.rating === "down" ? "default" : "outline"}
                      >
                        <ThumbsDown className="size-4" />
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">Agent output</p>
                    <div className="flex gap-2">
                      <Button
                        disabled={feedbackMutation.isPending}
                        onClick={() =>
                          feedbackMutation.mutate({ feedbackType: "agent", rating: "up" })
                        }
                        size="sm"
                        variant={agentFeedback?.rating === "up" ? "default" : "outline"}
                      >
                        <ThumbsUp className="size-4" />
                      </Button>
                      <Button
                        disabled={feedbackMutation.isPending}
                        onClick={() =>
                          feedbackMutation.mutate({ feedbackType: "agent", rating: "down" })
                        }
                        size="sm"
                        variant={agentFeedback?.rating === "down" ? "default" : "outline"}
                      >
                        <ThumbsDown className="size-4" />
                      </Button>
                    </div>
                  </div>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs" htmlFor="feedback-text">
                    Optional note (agent feedback)
                  </Label>
                  <FeedbackTextInput
                    feedbackMutation={feedbackMutation}
                    feedbackType="agent"
                    initialText={agentFeedback?.text}
                  />
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent className="space-y-4 mt-4" value="metadata">
            <div className="overflow-hidden rounded-md border border-border">
              <div className="border-b border-border bg-muted/50 px-3 py-2 text-xs font-semibold uppercase text-muted-foreground">
                Span Info
              </div>
              <Table>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground w-[35%]">
                      Name
                    </TableCell>
                    <TableCell className="text-xs">{span.scopeName || span.name || "—"}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Span ID
                    </TableCell>
                    <TableCell className="font-mono text-xs">{span.spanId}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Parent Span ID
                    </TableCell>
                    <TableCell className="font-mono text-xs">{span.parentSpanId ?? "—"}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Duration
                    </TableCell>
                    <TableCell className="text-xs">
                      {span.durationNano > 0
                        ? `${(span.durationNano / 1_000_000).toFixed(2)}ms`
                        : "—"}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Status
                    </TableCell>
                    <TableCell className="text-xs">{spanStatusLabel(span.statusCode)}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
            <AttributesTable attributes={span.resourceAttributes} title="Resource Attributes" />
            <AttributesTable attributes={span.spanAttributes} title="Span Attributes" />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
