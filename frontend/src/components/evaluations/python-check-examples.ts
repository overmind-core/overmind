export interface PythonCheckExample {
  id: string;
  label: string;
  source: string;
  spanType: string;
}

export const PYTHON_CHECK_HINT =
  "Read span.input, output, expected, duration_s, status, tool, children. Return True/False, a 0–1 score, or None to skip.";

export const PYTHON_CHECK_EXAMPLES: PythonCheckExample[] = [
  {
    id: "contains",
    label: "Contains text",
    source: `def evaluate(span):
    return "refund" in span.output.lower()
`,
    spanType: "llm_call",
  },
  {
    id: "pattern",
    label: "Matches pattern",
    source: String.raw`def evaluate(span):
    return re.search(r"ORD-\d{6}", span.output) is not None
`,
    spanType: "llm_call",
  },
  {
    id: "json",
    label: "Valid JSON",
    source: `def evaluate(span):
    try:
        data = json.loads(span.output)
    except ValueError:
        return False, "not valid JSON"
    return "team" in data, "checked key: team"
`,
    spanType: "llm_call",
  },
  {
    id: "latency",
    label: "Latency limit",
    source: `def evaluate(span):
    return span.duration_s <= 5, f"{round(span.duration_s, 1)}s"
`,
    spanType: "",
  },
  {
    id: "tools",
    label: "Tool order",
    source: `def evaluate(span):
    calls = [c.tool for c in span.children if c.tool]
    return calls == ["lookup_order", "issue_refund"], ", ".join(calls)
`,
    spanType: "workflow",
  },
];
