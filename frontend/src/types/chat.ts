export type ToolCallItem = {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
};

export type ChatMessage = {
  role?: string;
  content?: string | null;
  tool_calls?: ToolCallItem[];
  tool_call_id?: string;
};
