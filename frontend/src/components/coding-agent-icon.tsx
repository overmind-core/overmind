import ClaudeCode from "@lobehub/icons/es/ClaudeCode/components/Mono";
import Codex from "@lobehub/icons/es/Codex/components/Mono";
import Cursor from "@lobehub/icons/es/Cursor/components/Mono";
import GeminiCLI from "@lobehub/icons/es/GeminiCLI/components/Mono";

import { Icon } from "@/components/ui/icons";

export function CodingAgentIcon({ kind }: { kind: string }) {
  const Logo = { claude: ClaudeCode, codex: Codex, cursor: Cursor, gemini: GeminiCLI }[kind];
  if (Logo) return <Logo className="size-4 shrink-0" />;
  return kind === "coding_agent" ? (
    <Icon.agent className="size-4 shrink-0" />
  ) : (
    <Icon.project className="size-4 shrink-0" />
  );
}
