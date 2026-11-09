import type { WorkshopMessage } from "@/openapi";
import { workshopCatalog } from "./workshop-ui-catalog";

export function composerQuestion(props: unknown) {
  const parsed = workshopCatalog.Question.props.safeParse(props);
  if (!parsed.success || parsed.data.fields.length !== 1) return null;
  const field = parsed.data.fields[0];
  if (field.type !== "text" && field.type !== "textarea") return null;
  return { ...parsed.data, field, suggestion: field.placeholder || parsed.data.prompt };
}

function findQuestion(node: unknown, depth = 0): ReturnType<typeof composerQuestion> {
  if (depth > 24 || !node || typeof node !== "object") return null;
  const value = node as { component?: string; props?: Record<string, unknown> };
  if (value.component === "Question") return composerQuestion(value.props);
  for (const child of Object.values(node)) {
    if (child && typeof child === "object") {
      const found = findQuestion(child, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

export function workshopSuggestion(messages: WorkshopMessage[]) {
  for (const message of [...messages].reverse()) {
    if (["removed", "cancelled"].includes(message.status)) continue;
    if (message.role === "user") return null;
    if (!message.presentation) continue;
    const question = findQuestion(message.presentation.document.root);
    if (question) return { ...question, message: message.id };
  }
  return null;
}
