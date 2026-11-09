import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
} from "react";

import { Streamdown } from "streamdown";

import { Chart } from "@/components/tool-ui/chart";
import { DataTable as ToolDataTable } from "@/components/tool-ui/data-table/data-table";
import { sortData } from "@/components/tool-ui/data-table/utilities";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type {
  WorkshopCellReference,
  WorkshopInteraction,
  WorkshopMessage,
  WorkshopPresentation,
} from "@/openapi";
import { composerQuestion } from "./workshop-suggestion";
import { type WorkshopComponent, type WorkshopProps, workshopCatalog } from "./workshop-ui-catalog";
import "./workshop-presentation.css";

const PresentationContext = createContext<{
  messageId: string;
  value: WorkshopPresentation;
  cells: WorkshopCellReference[];
  onCell: (cell: WorkshopCellReference) => void;
  answers: Map<string, WorkshopInteraction>;
  withdrawn: Set<string>;
  reply: (id: string, values: Record<string, unknown>, prompt: string) => Promise<void>;
} | null>(null);

function usePresentation() {
  const context = useContext(PresentationContext);
  if (!context) throw new Error("Workshop presentation context is missing");
  return context;
}

function CellLink({ id, label }: { id: string; label?: string }) {
  const { cells, onCell } = usePresentation();
  const cell = cells.find((value) => value.id === id);
  if (!cell) return <span className="text-muted-foreground">Cell unavailable</span>;
  return (
    <Button
      className="min-w-0 max-w-full gap-1 text-xs font-normal"
      onClick={() => onCell(cell)}
      size="xs"
      variant="secondary"
    >
      <Icon.dataset className="size-3 shrink-0 text-cat-2" />
      <span className="truncate">{label || cell.name}</span>
    </Button>
  );
}

function Source({ name, image = false }: { name: string; image?: boolean }) {
  const { value, cells } = usePresentation();
  const reference = value.document[image ? "images" : "data"]?.[name];
  if (!reference) return null;
  return (
    <figcaption className="workshop-source-footer border border-border bg-wash-subtle px-3 py-2 text-xs leading-relaxed text-muted-foreground">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-x-3 gap-y-1">
          {(reference.cells as string[]).map((id) => {
            const cell = cells.find((entry) => entry.id === id);
            return (
              <div className="flex min-w-0 max-w-full items-center gap-2" key={id}>
                <CellLink id={id} />
                {!!cell?.columns?.length && (
                  <span className="shrink-0 tabular-nums">{cell.columns.length} columns</span>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </figcaption>
  );
}

function DataChart(props: WorkshopProps<"Chart">) {
  const { value } = usePresentation();
  const rows = value.data[props.data]?.rows;
  if (!rows?.length) return <p className="text-xs text-muted-foreground">No chart data</p>;
  return (
    <figure aria-label={props.title} className="workshop-chart min-w-0">
      <Tabs
        className="workshop-chart-views overflow-hidden rounded-sm border border-border"
        defaultValue="chart"
      >
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2">
          <span className="min-w-0 text-sm">{props.title}</span>
          <TabsList aria-label={`${props.title} view`} className="h-7 shrink-0">
            <TabsTrigger className="px-2 py-0.5 text-xs" value="chart">
              Chart
            </TabsTrigger>
            <TabsTrigger className="px-2 py-0.5 text-xs" value="table">
              Table
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent className="mt-0" value="chart">
          <Chart
            data={rows}
            description={[props.xLabel, props.yLabel].filter(Boolean).join(" · ")}
            id={`${props.data}-${props.kind}`}
            series={props.y.map((key) => ({ key, label: key }))}
            stacked={props.stacked}
            type={props.kind}
            xKey={props.x}
          />
        </TabsContent>
        <TabsContent className="mt-0" value="table">
          <DataTable
            columns={[props.x, ...props.y]}
            data={props.data}
            embedded
            title={props.title}
          />
        </TabsContent>
      </Tabs>
      <Source name={props.data} />
    </figure>
  );
}

const display = (value: unknown) =>
  value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);

function DataTable({
  embedded = false,
  ...props
}: WorkshopProps<"Table"> & { embedded?: boolean }) {
  const { value } = usePresentation();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<{ by?: string; direction?: "asc" | "desc" }>({});

  const rows = value.data[props.data]?.rows ?? [];
  const columns = useMemo(
    () => props.columns ?? [...new Set(rows.flatMap((row) => Object.keys(row)))],
    [props.columns, rows]
  );
  const rowIdKey = useMemo(() => {
    let key = "__workshop_row_id";
    while (rows.some((row) => Object.hasOwn(row, key))) key += "_";
    return key;
  }, [rows]);
  const identifiedRows = useMemo(
    () => rows.map((row, index) => ({ ...row, [rowIdKey]: String(index) })),
    [rows, rowIdKey]
  );
  const filtered = useMemo(() => {
    const result = identifiedRows.filter((row) =>
      columns.some((column) => display(row[column]).toLowerCase().includes(search.toLowerCase()))
    );
    return sort.by ? sortData(result, sort.by, sort.direction ?? "asc") : result;
  }, [identifiedRows, columns, search, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const current = Math.min(page, pages - 1);
  const download = () => {
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${props.data}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <figure aria-label={`${props.title} table`} className="workshop-data-table min-w-0">
      <div className={cn("overflow-hidden", !embedded && "rounded-sm border border-border")}>
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2">
          {!embedded && <div className="min-w-0 flex-1 text-sm font-semibold">{props.title}</div>}
          <div className="ml-auto flex w-44 max-w-[55%] shrink-0 items-stretch overflow-hidden rounded-sm border border-border bg-control focus-within:ring-2 focus-within:ring-ring/60">
            <Input
              aria-label={`Search ${props.title}`}
              className="min-w-0 flex-1 rounded-none border-0 focus-visible:ring-0"
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Filter rows…"
              size="xs"
              value={search}
            />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label="Download JSON"
                  className="shrink-0 rounded-none border-0 border-l border-l-border text-muted-foreground"
                  onClick={download}
                  size="icon-xs"
                  variant="ghost"
                >
                  <Icon.download />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Download JSON</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <ToolDataTable.Table
          columns={columns.map((column) => ({
            align: rows.some((row) => typeof row[column] === "number")
              ? ("right" as const)
              : ("left" as const),
            key: column,
            label: column,
            sortable: true,
          }))}
          data={filtered.slice(current * 10, current * 10 + 10)}
          id={props.data}
          onSortChange={(next) => {
            setSort(next);
            setPage(0);
          }}
          rowIdKey={rowIdKey}
          sort={sort}
        />
        {!filtered.length && <p className="text-xs text-muted-foreground">No matching rows</p>}
        {pages > 1 && (
          <div className="flex items-center justify-end gap-2 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
            <Button
              aria-label="Previous rows"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronLeft />
            </Button>
            <span>
              {current + 1} / {pages}
            </span>
            <Button
              aria-label="Next rows"
              disabled={current === pages - 1}
              onClick={() => setPage(current + 1)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronRight />
            </Button>
          </div>
        )}
      </div>
      {!embedded && <Source name={props.data} />}
    </figure>
  );
}

function Question(props: WorkshopProps<"Question"> | WorkshopProps<"Choices">) {
  const { answers, withdrawn, reply, messageId } = usePresentation();
  const retained = answers.get(props.id);
  const storageKey = `workshop-form:${messageId}:${props.id}`;
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    if (retained) return retained.values;
    const defaults =
      "fields" in props
        ? Object.fromEntries(
            props.fields
              .filter((field) => field.type === "boolean")
              .map((field) => [field.name, false])
          )
        : {};
    try {
      return { ...defaults, ...JSON.parse(sessionStorage.getItem(storageKey) || "{}") };
    } catch {
      return defaults;
    }
  });
  const [submitted, setSubmitted] = useState(false);
  const wasWithdrawn = withdrawn.has(props.id);
  useEffect(() => {
    if (wasWithdrawn) setSubmitted(false);
  }, [wasWithdrawn]);
  useEffect(() => {
    try {
      if (retained || submitted) sessionStorage.removeItem(storageKey);
      else sessionStorage.setItem(storageKey, JSON.stringify(values));
    } catch {
      /* Storage can be unavailable; the form remains usable. */
    }
  }, [values, storageKey, retained, submitted]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const disabled = pending || !!retained || submitted;
  const set = (name: string, value: unknown) =>
    setValues((current) => ({ ...current, [name]: value }));
  const submit = async (response = values) => {
    if (disabled) return;
    setPending(true);
    setError(null);
    try {
      if (
        "fields" in props &&
        props.fields.some(
          (field) =>
            field.required &&
            (response[field.name] == null ||
              response[field.name] === "" ||
              (Array.isArray(response[field.name]) && !(response[field.name] as unknown[]).length))
        )
      ) {
        setError("Complete the required fields.");
        return;
      }
      await reply(props.id, response, props.prompt);
      setSubmitted(true);
    } catch {
      setError("Response could not be sent. Try again.");
    } finally {
      setPending(false);
    }
  };
  return (
    <form
      aria-label={props.prompt}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="text-sm">{props.prompt}</p>
      {"options" in props ? (
        <div className="flex flex-wrap gap-2">
          {props.options.map((option) => (
            <Button
              aria-pressed={(retained?.values ?? values).choice === option.value}
              disabled={disabled}
              key={option.value}
              onClick={() => {
                set("choice", option.value);
                void submit({ choice: option.value });
              }}
              size="xs"
              type="button"
              variant="secondary"
            >
              {option.label}
            </Button>
          ))}
        </div>
      ) : (
        <>
          <fieldset className="space-y-3" disabled={disabled}>
            {props.fields.map((field) => {
              const fieldId = `${id}-${field.name}`;
              const selected = (retained?.values ?? values)[field.name];
              return (
                <div className="space-y-1.5" key={field.name}>
                  <Label className="text-xs text-muted-foreground" htmlFor={fieldId}>
                    {field.label}
                    {field.required ? " *" : ""}
                  </Label>
                  {field.type === "text" || field.type === "number" ? (
                    <Input
                      id={fieldId}
                      max={field.max}
                      min={field.min}
                      onChange={(e) =>
                        set(
                          field.name,
                          field.type === "number" && e.target.value !== ""
                            ? Number(e.target.value)
                            : e.target.value
                        )
                      }
                      placeholder={field.placeholder}
                      required={field.required}
                      size="sm"
                      step="any"
                      type={field.type}
                      value={
                        typeof selected === "string" || typeof selected === "number" ? selected : ""
                      }
                    />
                  ) : field.type === "textarea" ? (
                    <Textarea
                      className="min-h-16 text-sm"
                      id={fieldId}
                      maxLength={8000}
                      onChange={(e) => set(field.name, e.target.value)}
                      placeholder={field.placeholder}
                      required={field.required}
                      value={typeof selected === "string" ? selected : ""}
                    />
                  ) : field.type === "boolean" ? (
                    <Checkbox
                      checked={selected === true}
                      id={fieldId}
                      onCheckedChange={(checked) => set(field.name, checked === true)}
                    />
                  ) : field.type === "select" ? (
                    <Select
                      disabled={disabled}
                      onValueChange={(value) => set(field.name, value)}
                      required={field.required}
                      value={typeof selected === "string" ? selected : ""}
                    >
                      <SelectTrigger className="w-full" id={fieldId} size="sm">
                        <SelectValue placeholder={field.placeholder || "Select…"} />
                      </SelectTrigger>
                      <SelectContent>
                        {field.options?.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                      {field.options?.map((option) => (
                        <label className="flex items-center gap-2 text-xs" key={option.value}>
                          <Checkbox
                            checked={Array.isArray(selected) && selected.includes(option.value)}
                            onCheckedChange={(checked) =>
                              set(
                                field.name,
                                checked
                                  ? [...(Array.isArray(selected) ? selected : []), option.value]
                                  : (Array.isArray(selected) ? selected : []).filter(
                                      (item) => item !== option.value
                                    )
                              )
                            }
                          />
                          {option.label}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </fieldset>
          <Button disabled={disabled} size="xs" type="submit" variant="secondary">
            {pending ? <Spinner size="sm" /> : null}
            {retained || submitted ? "Sent" : props.submitLabel || "Send response"}
          </Button>
        </>
      )}
      {(retained || submitted) && "options" in props && (
        <p className="text-xs text-muted-foreground">Response sent</p>
      )}
      {error && (
        <p className="text-xs text-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function component<K extends WorkshopComponent>(
  name: K,
  render: (props: WorkshopProps<K>, renderNode: (node: unknown) => ReactNode) => ReactNode
) {
  const entry = workshopCatalog[name];
  return {
    name,
    render: (props: unknown, renderNode: (node: unknown) => ReactNode) =>
      render(entry.props.parse(props) as WorkshopProps<K>, renderNode),
  };
}

function Metric({
  metricKey,
  ...props
}: Omit<WorkshopProps<"Metric">, "key"> & { metricKey: string }) {
  const { value } = usePresentation();
  const raw = value.data[props.data]?.rows[0]?.[metricKey];
  const formatted =
    typeof raw === "number"
      ? new Intl.NumberFormat(undefined, {
          maximumFractionDigits: 2,
          style: props.format === "percent" ? "percent" : "decimal",
        }).format(raw)
      : display(raw);
  return (
    <figure aria-label={props.label} className="min-w-0">
      <div className="text-xs text-muted-foreground">{props.label}</div>
      <div className="mt-1 text-lg">{formatted}</div>
      <Source name={props.data} />
    </figure>
  );
}

function PlotImage(props: WorkshopProps<"Image">) {
  const { value } = usePresentation();
  const url = value.images[props.image]?.url;
  return (
    <figure>
      <img alt={props.title} className="max-h-96 max-w-full object-contain" src={url} />
      <Source image name={props.image} />
    </figure>
  );
}

const components = [
  component("Stack", (props, renderNode) => (
    <div
      className={cn(
        "flex min-w-0 gap-5",
        props.direction === "row" ? "flex-row flex-wrap [&>*]:min-w-0 [&>*]:flex-1" : "flex-col"
      )}
    >
      {renderNode(props.children)}
    </div>
  )),
  component("Text", (props) => (
    <Streamdown
      className="workshop-message-text text-sm leading-relaxed"
      components={{ img: ({ alt }) => <span>{alt}</span> }}
      controls={false}
    >
      {props.text}
    </Streamdown>
  )),
  component("Heading", (props) => <p className="text-sm font-semibold">{props.text}</p>),
  component("Callout", (props) =>
    props.tone === "info" || props.tone === "neutral" ? null : (
      <p
        className={cn(
          "border-l-2 border-border pl-3 text-sm leading-relaxed text-muted-foreground",
          props.tone === "warning" && "border-warning text-warning",
          props.tone === "error" && "border-error text-error",
          props.tone === "success" && "border-success text-success"
        )}
      >
        {props.text}
      </p>
    )
  ),
  component("Chart", (props) => <DataChart {...props} />),
  component("Table", (props) => <DataTable {...props} />),
  component("Metric", ({ key, ...props }) => <Metric {...props} metricKey={key} />),
  component("Cell", (props) => <CellLink id={props.cell} label={props.label} />),
  component("Image", (props) => <PlotImage {...props} />),
  component("Details", (props, renderNode) => (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {props.title}
      </summary>
      <div className="mt-3 space-y-4">{renderNode(props.children)}</div>
    </details>
  )),
  component("Tabs", (props, renderNode) => (
    <Tabs defaultValue="0">
      <TabsList className="h-7 justify-start gap-3 bg-transparent p-0">
        {props.tabs.map((tab, i) => (
          <TabsTrigger
            className="px-0 py-1 text-xs data-[state=active]:bg-transparent data-[state=active]:text-primary"
            key={tab.title}
            value={String(i)}
          >
            {tab.title}
          </TabsTrigger>
        ))}
      </TabsList>
      {props.tabs.map((tab, i) => (
        <TabsContent className="space-y-4 pt-2" key={tab.title} value={String(i)}>
          {renderNode(tab.children)}
        </TabsContent>
      ))}
    </Tabs>
  )),
  component("Steps", (props) => (
    <ol className="space-y-2 text-xs">
      {props.items.map((item, index) => (
        <li className="flex items-start gap-2" key={`${index}-${item.text}`}>
          <span className="text-muted-foreground">{index + 1}.</span>
          <span className="flex-1">{item.text}</span>
          <span className={cn("text-muted-foreground", item.status === "failed" && "text-error")}>
            {item.status}
          </span>
        </li>
      ))}
    </ol>
  )),
  component("Code", (props) => (
    <div className="space-y-1">
      <span className="text-xs text-muted-foreground">{props.language}</span>
      <pre className="max-h-64 overflow-auto rounded-sm bg-wash-raised p-2 text-xs leading-relaxed">
        <code>{props.code}</code>
      </pre>
    </div>
  )),
  component("Question", (props) =>
    composerQuestion(props) ? <p className="text-sm">{props.prompt}</p> : <Question {...props} />
  ),
  component("Choices", (props) => <Question {...props} />),
];

function isSummary(node: unknown) {
  if (!node || typeof node !== "object") return false;
  const value = node as { component?: string; props?: { tone?: string } };
  return value.component === "Callout" && ["info", "neutral"].includes(value.props?.tone ?? "");
}

function renderNode(node: unknown, depth = 0): ReactNode {
  if (depth > 24) throw new Error("Result nesting is too deep");
  if (Array.isArray(node))
    return node
      .filter((item) => !isSummary(item))
      .map((item, index) => (
        <div className="min-w-0" key={index}>
          {renderNode(item, depth + 1)}
        </div>
      ));
  if (!node || typeof node !== "object") throw new Error("Invalid result");
  const value = node as { component?: string; props?: unknown };
  const entry = components.find((item) => item.name === value.component);
  if (!entry) throw new Error("Unknown result component");
  return entry.render(value.props, (child) => renderNode(child, depth + 1));
}

export default function WorkshopPresentationView({
  message,
  messages,
  onCell,
  onSend,
}: {
  message: WorkshopMessage;
  messages: WorkshopMessage[];
  onCell: (cell: WorkshopCellReference) => void;
  onSend: (text: string, interaction?: WorkshopInteraction) => Promise<void>;
}) {
  const value = message.presentation!;
  let content: ReactNode;
  try {
    content = renderNode(value.document.root);
  } catch {
    content = (
      <p className="text-xs text-error" role="alert">
        This result could not be displayed. Ask the agent to regenerate it.
      </p>
    );
  }
  const responses = new Map(
    messages
      .filter((item) => item.interaction?.message === message.id)
      .map((item) => [item.interaction!.component, item])
  );
  const withdrawn = new Set(
    [...responses]
      .filter(([, item]) => ["removed", "cancelled"].includes(item.status))
      .map(([key]) => key)
  );
  const answers = new Map(
    [...responses]
      .filter(([key]) => !withdrawn.has(key))
      .map(([key, item]) => [key, item.interaction!])
  );
  return (
    <PresentationContext.Provider
      value={{
        answers,
        cells: message.cells ?? [],
        messageId: message.id,
        onCell,
        reply: (component, values, prompt) =>
          onSend(prompt, { component, message: message.id, values }),
        value,
        withdrawn,
      }}
    >
      <div className="workshop-presentation min-w-0">{content}</div>
    </PresentationContext.Provider>
  );
}
