export type ToolCallItem = {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
  name?: string;
  arguments?: unknown;
};

export type TranscriptMessage = {
  role?: string;
  content?: unknown;
  parts?: unknown[];
  tool_calls?: ToolCallItem[];
  tool_call_id?: string;
  name?: string;
};
