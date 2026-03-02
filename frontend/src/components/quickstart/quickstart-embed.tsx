import { Link } from "@tanstack/react-router";
import { ArrowRight } from "pixelarticons/react";

import { Button } from "@/components/ui/button";

import { QuickstartSnippets } from "./quickstart-snippets";

export function QuickstartEmbed() {
  return (
    <div className="flex w-full flex-col items-center py-4">
      <p className="mb-1 font-display text-4xl font-medium">No agents detected yet</p>
      <p className="mx-auto mb-6 max-w-md text-sm text-muted-foreground">
        Connect your LLM application to start tracing. Pick your language and provider below, then
        copy the snippet into your project.
      </p>

      <div className="w-full max-w-2xl">
        <QuickstartSnippets compact />
      </div>

      <Button asChild className="mt-4" size="sm" variant="outline">
        <Link to="/get-started">
          View full setup guide <ArrowRight className="ml-1.5 size-3.5" />
        </Link>
      </Button>
    </div>
  );
}
