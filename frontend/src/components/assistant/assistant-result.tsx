import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { lazyChart } from "@/components/ui/lazy-chart";
import { MarkdownContent } from "@/components/ui/markdown";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SortableHeader } from "@/components/ui/sortable-header";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTableSort } from "@/hooks/use-table-sort";
import { cn } from "@/lib/utils";
import type {
  ChartNode,
  ChoicesNode,
  Interaction,
  Presentation,
  QuestionNode,
  ResultNode,
  TableNode,
} from "./types";

const PAGE_ROWS = 10;
const AssistantChart = lazyChart(() => import("./assistant-chart"), { minHeight: 200 });

/** What identifies one Question or Choices in a thread: the model may reuse an id. */
export const answerKey = (call: string, id: string) => `${call}:${id}`;

export interface ResultActions {
  /** The `answerKey` of every Question and Choices the user already answered. */
  answered: ReadonlySet<string>;
  sending: boolean;
  onAnswer: (text: string, interaction: Interaction) => void;
  onCell: (cell: string, dataset: string) => void;
  onTrain: (dataset: string) => void;
  /** The bytes of an image this thread retained, by content name. */
  loadImage: (name: string) => Promise<Blob>;
}

const Context = createContext<{
  call: string;
  presentation: Presentation;
  actions: ResultActions;
} | null>(null);
const useResult = () => useContext(Context)!;

function CellChip({ id, label }: { id: string; label: string }) {
  const { presentation, actions } = useResult();
  const cell = presentation.cells[id];
  if (!cell) return <span className="text-muted-foreground">Version unavailable</span>;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            className="min-w-0 max-w-full gap-1 text-xs font-normal"
            onClick={() => actions.onCell(id, cell.dataset)}
            size="xs"
            variant="secondary"
          >
            <Icon.dataset className="size-3 shrink-0 text-cat-2" />
            <span className="truncate">{label || cell.name}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          {cell.title} · {cell.rows.toLocaleString()} rows
        </TooltipContent>
      </Tooltip>
      <Button onClick={() => actions.onTrain(cell.dataset)} size="xs">
        <Icon.training />
        Train
      </Button>
    </div>
  );
}

function ResultTable({ node, embedded = false }: { node: TableNode; embedded?: boolean }) {
  const { presentation } = useResult();
  const rows = presentation.data[node.data] ?? [];
  const columns = node.columns.length ? node.columns : Object.keys(rows[0] ?? {});
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const { onSort, sort, sortRows } = useTableSort<string>();
  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const kept = needle
      ? rows.filter((row) =>
          columns.some((column) =>
            String(row[column] ?? "")
              .toLowerCase()
              .includes(needle)
          )
        )
      : rows;
    return sortRows(kept, (row, key) => row[key]);
  }, [columns, filter, rows, sortRows]);
  const pages = Math.ceil(filtered.length / PAGE_ROWS);
  const current = Math.min(page, Math.max(0, pages - 1));
  const numeric = (column: string) => rows.some((row) => typeof row[column] === "number");
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(filtered, null, 2)], { type: "application/json" })
    );
    Object.assign(document.createElement("a"), {
      download: `${node.data}.json`,
      href: url,
    }).click();
    URL.revokeObjectURL(url);
  };

  return (
    <figure aria-label={`${node.title} table`} className="min-w-0">
      <div className={cn("overflow-hidden", !embedded && "rounded-sm border border-border")}>
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2">
          {!embedded && <div className="min-w-0 flex-1 text-sm font-semibold">{node.title}</div>}
          <div className="ml-auto flex w-44 max-w-[55%] shrink-0 items-stretch overflow-hidden rounded-sm border border-border bg-control focus-within:ring-2 focus-within:ring-ring/60">
            <Input
              aria-label={`Search ${node.title}`}
              className="min-w-0 flex-1 rounded-none border-0 focus-visible:ring-0"
              onChange={(event) => {
                setFilter(event.target.value);
                setPage(0);
              }}
              placeholder="Filter rows…"
              value={filter}
            />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label="Download JSON"
                  className="shrink-0 rounded-none border-0 border-l border-l-border text-muted-foreground"
                  onClick={download}
                  size="icon-sm"
                  variant="ghost"
                >
                  <Icon.download />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Download JSON</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                {columns.map((column) => (
                  <SortableHeader
                    className={cn(numeric(column) && "text-right")}
                    key={column}
                    label={column}
                    onSort={onSort}
                    sort={sort}
                    sortKey={column}
                  />
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.slice(current * PAGE_ROWS, (current + 1) * PAGE_ROWS).map((row, index) => (
                <TableRow key={index}>
                  {columns.map((column) => (
                    <TableCell
                      className={cn(
                        "tabular-nums",
                        typeof row[column] === "number" && "text-right"
                      )}
                      key={column}
                    >
                      {row[column] == null || row[column] === "" ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        String(row[column])
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {!filtered.length && (
          <p className="px-3 py-2 text-xs text-muted-foreground">No matching rows</p>
        )}
        {pages > 1 && (
          <div className="flex items-center justify-end gap-2 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
            <span className="tabular-nums">
              {current + 1} of {pages}
            </span>
            <Button
              aria-label="Previous rows"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronLeft />
            </Button>
            <Button
              aria-label="Next rows"
              disabled={current >= pages - 1}
              onClick={() => setPage(current + 1)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronRight />
            </Button>
          </div>
        )}
      </div>
    </figure>
  );
}

function ResultChart({ node }: { node: ChartNode }) {
  const { presentation } = useResult();
  const rows = presentation.data[node.data];
  if (!rows?.length) return <p className="text-xs text-muted-foreground">No chart data</p>;
  return (
    <figure aria-label={node.title} className="min-w-0">
      <Tabs className="overflow-hidden rounded-sm border border-border" defaultValue="chart">
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2">
          <div className="min-w-0">
            <p className="text-sm">{node.title}</p>
            {(node.x_label || node.y_label) && (
              <p className="text-xs text-muted-foreground">
                {[node.x_label, node.y_label].filter(Boolean).join(" · ")}
              </p>
            )}
          </div>
          <TabsList aria-label={`${node.title} view`} className="h-7 shrink-0">
            <TabsTrigger className="px-2 py-0.5 text-xs" value="chart">
              Chart
            </TabsTrigger>
            <TabsTrigger className="px-2 py-0.5 text-xs" value="table">
              Table
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent className="mt-0" value="chart">
          <AssistantChart node={node} rows={rows} />
        </TabsContent>
        <TabsContent className="mt-0" value="table">
          <ResultTable
            embedded
            node={{
              columns: [node.x, ...node.y],
              data: node.data,
              title: node.title,
              type: "Table",
            }}
          />
        </TabsContent>
      </Tabs>
    </figure>
  );
}

function Metric({ node }: { node: Extract<ResultNode, { type: "Metric" }> }) {
  const value = useResult().presentation.data[node.data]?.[0]?.[node.key];
  const shown =
    value == null
      ? "Not measured"
      : node.format === "percent" && typeof value === "number"
        ? `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
        : typeof value === "number"
          ? value.toLocaleString()
          : String(value);
  return (
    <figure aria-label={node.label} className="min-w-0">
      <div className="text-xs text-muted-foreground">{node.label}</div>
      <div className="mt-1 text-lg tabular-nums">{shown}</div>
    </figure>
  );
}

function ResultImage({ node }: { node: Extract<ResultNode, { type: "Image" }> }) {
  const { presentation, actions } = useResult();
  const name = presentation.images[node.image];
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  const { loadImage } = actions;
  useEffect(() => {
    let made: string | undefined;
    let gone = false;
    loadImage(name).then(
      (blob) => {
        if (gone) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      },
      () => setFailed(true)
    );
    return () => {
      gone = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [loadImage, name]);
  return (
    <figure
      aria-label={node.title}
      className="min-w-0 overflow-hidden rounded-sm border border-border"
    >
      <figcaption className="border-b border-border/70 px-3 py-2 text-sm">{node.title}</figcaption>
      {failed ? (
        <p className="px-3 py-2 text-xs text-muted-foreground">The image could not be loaded.</p>
      ) : url ? (
        <img alt={node.title} className="max-h-96 w-full bg-card object-contain" src={url} />
      ) : (
        <Skeleton className="h-48 w-full rounded-none" />
      )}
    </figure>
  );
}

function draftOf(key: string): Record<string, unknown> {
  try {
    return JSON.parse(sessionStorage.getItem(key) ?? "{}");
  } catch {
    return {};
  }
}

function Question({ node }: { node: QuestionNode | ChoicesNode }) {
  const { actions, call } = useResult();
  const key = answerKey(call, node.id);
  // A draft outlives a closed panel, within this browser session.
  const draftKey = `assistant-answer:${key}`;
  const [values, setValues] = useState<Record<string, unknown>>(() => draftOf(draftKey));
  const done = actions.answered.has(key);
  const disabled = done || actions.sending;
  const set = (name: string, value: unknown) =>
    setValues((old) => {
      const next = { ...old, [name]: value };
      sessionStorage.setItem(draftKey, JSON.stringify(next));
      return next;
    });
  const answer = (given: Record<string, unknown>, text: string) => {
    sessionStorage.removeItem(draftKey);
    actions.onAnswer(text, { call, id: node.id, values: given });
  };

  if (node.type === "Choices")
    return (
      <div className="space-y-3">
        <p className="text-sm">{node.prompt}</p>
        <div className="flex flex-wrap gap-2">
          {node.options.map((option) => (
            <Button
              disabled={disabled}
              key={option.value}
              onClick={() => answer({ choice: option.value }, option.label)}
              size="sm"
              variant="secondary"
            >
              {option.label}
            </Button>
          ))}
        </div>
        {done && <p className="text-xs text-muted-foreground">Response sent</p>}
      </div>
    );

  const missing = node.fields.some(
    (field) => field.required && (values[field.name] == null || values[field.name] === "")
  );
  return (
    <form
      aria-label={node.prompt}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        const text = node.fields
          .map((field) => `${field.label}: ${String(values[field.name] ?? "")}`)
          .join("\n");
        answer(values, text);
      }}
    >
      <p className="text-sm">{node.prompt}</p>
      <fieldset className="space-y-3" disabled={disabled}>
        {node.fields.map((field) => {
          const id = `${node.id}-${field.name}`;
          const value = values[field.name];
          return (
            <div className="space-y-1.5" key={field.name}>
              <Label className="text-xs text-muted-foreground" htmlFor={id}>
                {field.label}
              </Label>
              {field.type === "textarea" ? (
                <Textarea
                  className="min-h-16 text-sm"
                  id={id}
                  onChange={(event) => set(field.name, event.target.value)}
                  placeholder={field.placeholder}
                  value={String(value ?? "")}
                />
              ) : field.type === "select" ? (
                <Select onValueChange={(next) => set(field.name, next)} value={String(value ?? "")}>
                  <SelectTrigger className="w-full" id={id} size="sm">
                    <SelectValue placeholder={field.placeholder || "Select…"} />
                  </SelectTrigger>
                  <SelectContent>
                    {field.options.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : field.type === "multiselect" ? (
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {field.options.map((option) => {
                    const chosen = Array.isArray(value) ? (value as string[]) : [];
                    return (
                      <label className="flex items-center gap-2 text-xs" key={option.value}>
                        <Checkbox
                          checked={chosen.includes(option.value)}
                          onCheckedChange={(checked) =>
                            set(
                              field.name,
                              checked
                                ? [...chosen, option.value]
                                : chosen.filter((item) => item !== option.value)
                            )
                          }
                        />
                        {option.label}
                      </label>
                    );
                  })}
                </div>
              ) : field.type === "boolean" ? (
                <Checkbox
                  checked={value === true}
                  id={id}
                  onCheckedChange={(checked) => set(field.name, checked === true)}
                />
              ) : (
                <Input
                  id={id}
                  max={field.max ?? undefined}
                  min={field.min ?? undefined}
                  onChange={(event) =>
                    set(
                      field.name,
                      field.type === "number" && event.target.value !== ""
                        ? Number(event.target.value)
                        : event.target.value
                    )
                  }
                  placeholder={field.placeholder}
                  type={field.type === "number" ? "number" : "text"}
                  value={String(value ?? "")}
                />
              )}
            </div>
          );
        })}
        <Button disabled={missing} size="sm" type="submit">
          {node.submit_label || "Send"}
        </Button>
      </fieldset>
      {done && <p className="text-xs text-muted-foreground">Response sent</p>}
    </form>
  );
}

const CALLOUT = {
  error: "border-error text-error",
  info: "border-info text-muted-foreground",
  neutral: "border-border text-muted-foreground",
  success: "border-success text-success",
  warning: "border-warning text-warning",
};

function Nodes({ nodes }: { nodes: ResultNode[] }): ReactNode {
  return nodes.map((node, index) => (
    <div className="min-w-0" key={index}>
      <Node node={node} />
    </div>
  ));
}

function Node({ node }: { node: ResultNode }): ReactNode {
  switch (node.type) {
    case "Stack":
      return (
        <div
          className={cn(
            "flex min-w-0 gap-5",
            node.direction === "row" ? "flex-row flex-wrap [&>*]:min-w-0 [&>*]:flex-1" : "flex-col"
          )}
        >
          <Nodes nodes={node.children} />
        </div>
      );
    case "Text":
      return <MarkdownContent className="break-words">{node.text}</MarkdownContent>;
    case "Heading":
      return <p className="text-sm font-semibold">{node.text}</p>;
    case "Callout":
      return (
        <p className={cn("border-l-2 pl-3 text-sm leading-relaxed", CALLOUT[node.tone])}>
          {node.text}
        </p>
      );
    case "Chart":
      return <ResultChart node={node} />;
    case "Table":
      return <ResultTable node={node} />;
    case "Metric":
      return <Metric node={node} />;
    case "Image":
      return <ResultImage node={node} />;
    case "Cell":
      return <CellChip id={node.cell} label={node.label} />;
    case "Details":
      return (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {node.title}
          </summary>
          <div className="mt-3 space-y-4">
            <Nodes nodes={node.children} />
          </div>
        </details>
      );
    case "Tabs":
      return (
        <Tabs defaultValue="0">
          <TabsList className="h-7 justify-start gap-3 bg-transparent p-0">
            {node.tabs.map((tab, index) => (
              <TabsTrigger
                className="px-0 py-1 text-xs data-[state=active]:bg-transparent data-[state=active]:text-primary"
                key={tab.title}
                value={String(index)}
              >
                {tab.title}
              </TabsTrigger>
            ))}
          </TabsList>
          {node.tabs.map((tab, index) => (
            <TabsContent className="space-y-4 pt-2" key={tab.title} value={String(index)}>
              <Nodes nodes={tab.children} />
            </TabsContent>
          ))}
        </Tabs>
      );
    case "Steps":
      return (
        <ol className="space-y-2 text-xs">
          {node.items.map((item, index) => (
            <li className="flex items-start gap-2" key={item.text}>
              <span className="text-muted-foreground">{index + 1}.</span>
              <span className="flex-1">{item.text}</span>
              <span
                className={cn("text-muted-foreground", item.status === "failed" && "text-error")}
              >
                {item.status}
              </span>
            </li>
          ))}
        </ol>
      );
    case "Code":
      return (
        <div className="space-y-1">
          <span className="text-xs text-muted-foreground">{node.language}</span>
          <pre className="max-h-64 overflow-auto rounded-sm bg-wash-raised p-2 text-xs leading-relaxed">
            {node.code}
          </pre>
        </div>
      );
    case "Question":
    case "Choices":
      return <Question node={node} />;
    default:
      return <p className="text-xs text-muted-foreground">This part cannot be displayed.</p>;
  }
}

export function AssistantResult({
  call,
  presentation,
  actions,
}: {
  /** The tool call that showed this result. */
  call: string;
  presentation: Presentation;
  actions: ResultActions;
}) {
  const value = useMemo(() => ({ actions, call, presentation }), [actions, call, presentation]);
  return (
    <Context.Provider value={value}>
      <div className="assistant-result min-w-0">
        <Node node={presentation.root} />
      </div>
    </Context.Provider>
  );
}
