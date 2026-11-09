import { z } from "zod/v4";

const children = z.array(z.unknown()).max(64);
const text = z.string().max(16000);
const name = z.string().min(1).max(160);
const field = z.object({
  label: name,
  max: z.number().optional(),
  min: z.number().optional(),
  name,
  options: z
    .array(z.object({ label: name, value: name }))
    .max(100)
    .optional(),
  placeholder: text.optional(),
  required: z.boolean().optional(),
  type: z.enum(["text", "textarea", "number", "select", "multiselect", "boolean"]),
});

export const workshopCatalog = {
  Callout: {
    description: "An assumption, limitation, finding or warning.",
    props: z.object({ text, tone: z.enum(["neutral", "info", "success", "warning", "error"]) }),
  },
  Cell: {
    description: "Open an exact published Workshop cell. Use its UUID.",
    props: z.object({ cell: z.uuid(), label: name.optional() }),
  },
  Chart: {
    description:
      "Plot a retained data table by key. Values come from its rows; never inline model estimates. Use bar for computed histogram bins.",
    props: z.object({
      data: name,
      kind: z.enum(["bar", "horizontal-bar", "line", "area", "pie", "scatter"]),
      stacked: z.boolean().optional(),
      title: name,
      x: name,
      xLabel: name.optional(),
      y: z.array(name).min(1).max(8),
      yLabel: name.optional(),
    }),
  },
  Choices: {
    description:
      "Offer meaningful next steps or resolve an ambiguity. Selection becomes a native Workshop follow-up.",
    props: z.object({
      id: name,
      options: z
        .array(z.object({ label: name, value: name }))
        .min(1)
        .max(12),
      prompt: text,
    }),
  },
  Code: {
    description: "Retained code or a proposed recipe, displayed without execution.",
    props: z.object({ code: text, language: name.optional() }),
  },
  Details: {
    description: "Optional explanation or evidence behind a disclosure.",
    props: z.object({ children, title: name }),
  },
  Heading: { description: "A short section heading.", props: z.object({ text: name }) },
  Image: {
    description: "Display a retained PNG, JPEG or WebP plot by image key.",
    props: z.object({ image: name, title: name }),
  },
  Metric: {
    description:
      "A scalar from the first row of a retained data table. Format percent expects a fraction.",
    props: z.object({
      data: name,
      format: z.enum(["number", "percent", "text"]).optional(),
      key: name,
      label: name,
    }),
  },
  Question: {
    description:
      "Collect training intent, mappings, constraints or transformation choices. Submission becomes a native Workshop follow-up, not a direct platform mutation.",
    props: z.object({
      fields: z.array(field).min(1).max(20),
      id: name,
      prompt: text,
      submitLabel: name.optional(),
    }),
  },
  Stack: {
    description: "Group content vertically, or in a wrapping row. Use as the root.",
    props: z.object({ children, direction: z.enum(["column", "row"]).optional() }),
  },
  Steps: {
    description: "Public plan or recorded progress. Only mark steps done when executed.",
    props: z.object({
      items: z
        .array(z.object({ status: z.enum(["pending", "running", "completed", "failed"]), text }))
        .min(1)
        .max(30),
    }),
  },
  Table: {
    description:
      "Searchable, sortable, paginated rows from a retained data table. Omit columns to show all.",
    props: z.object({ columns: z.array(name).min(1).max(40).optional(), data: name, title: name }),
  },
  Tabs: {
    description: "Related views, such as distribution and data quality.",
    props: z.object({
      tabs: z
        .array(z.object({ children, title: name }))
        .min(1)
        .max(8),
    }),
  },
  Text: {
    description: "Explanation in Markdown; measured data belongs in data components.",
    props: z.object({ text }),
  },
} as const;

export type WorkshopComponent = keyof typeof workshopCatalog;
export type WorkshopProps<K extends WorkshopComponent> = z.infer<
  (typeof workshopCatalog)[K]["props"]
>;
