import { Sparkles } from "lucide-react";

import type { SuggestionOut } from "@/api";
import { SuggestionCard } from "@/components/suggestion-card";

export function SuggestionsTab({ suggestions }: { suggestions: SuggestionOut[] }) {
  if (suggestions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center border border-dashed border-border py-16">
        <Sparkles className="mb-3 size-12 text-muted-foreground/50" />
        <p className="text-center text-sm italic text-muted-foreground">
          No suggestions yet — tune the prompt to generate suggestions.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {suggestions.map((e) => (
        <SuggestionCard key={e.id} suggestion={e} />
      ))}
    </div>
  );
}
