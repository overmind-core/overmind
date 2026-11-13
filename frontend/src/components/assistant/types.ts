export interface ToolCall {
  id: string;
  name: string;
  raw: string;
}

/** An answer to one Question or Choices: the tool call that showed it and its id there. */
export interface Interaction {
  call: string;
  id: string;
  values: Record<string, unknown>;
}

export type Row = Record<string, string | number | boolean | null>;

export interface Presentation {
  root: ResultNode;
  data: Record<string, Row[]>;
  /** Image key to the content name the thread serves it under. */
  images: Record<string, string>;
  cells: Record<string, { dataset: string; title: string; rows: number; name: string }>;
}

interface Envelope<K extends string, P> {
  sequence: number;
  kind: K;
  payload: P;
}

export type AssistantEvent =
  | Envelope<
      "user",
      { input: string; text: string; cells: string[]; interaction: Interaction | null }
    >
  | Envelope<"assistant", { text: string; calls: ToolCall[] }>
  | (Envelope<"tool_result", { call: string; name: string; ok: boolean; content: unknown }> & {
      presentation: Presentation | null;
    })
  | Envelope<"status", { state: "stopped" | "failed" | "interrupted"; detail: string }>
  | Envelope<"summary", { text: string; through: number }>;

export interface Draft {
  text: string;
  tools: string[];
}

interface Option {
  label: string;
  value: string;
}

interface FormField {
  name: string;
  label: string;
  type: "text" | "textarea" | "number" | "select" | "multiselect" | "boolean";
  options: Option[];
  placeholder: string;
  required: boolean;
  min: number | null;
  max: number | null;
}

export type QuestionNode = {
  type: "Question";
  id: string;
  prompt: string;
  fields: FormField[];
  submit_label: string;
};
export type ChoicesNode = { type: "Choices"; id: string; prompt: string; options: Option[] };
export type ChartNode = {
  type: "Chart";
  title: string;
  kind: "bar" | "horizontal-bar" | "line" | "area" | "pie" | "scatter";
  data: string;
  x: string;
  y: string[];
  stacked: boolean;
  x_label: string;
  y_label: string;
};
export type TableNode = { type: "Table"; title: string; data: string; columns: string[] };

/** The components the backend validates in `services/assistant/presentation.py`. */
export type ResultNode =
  | { type: "Callout"; tone: "neutral" | "info" | "success" | "warning" | "error"; text: string }
  | { type: "Cell"; cell: string; label: string }
  | ChartNode
  | ChoicesNode
  | { type: "Code"; code: string; language: string }
  | { type: "Details"; title: string; children: ResultNode[] }
  | { type: "Heading"; text: string }
  | { type: "Image"; title: string; image: string }
  | {
      type: "Metric";
      label: string;
      data: string;
      key: string;
      format: "number" | "percent" | "text";
    }
  | QuestionNode
  | { type: "Stack"; direction: "column" | "row"; children: ResultNode[] }
  | {
      type: "Steps";
      items: { status: "pending" | "running" | "completed" | "failed"; text: string }[];
    }
  | TableNode
  | { type: "Tabs"; tabs: { title: string; children: ResultNode[] }[] }
  | { type: "Text"; text: string };
