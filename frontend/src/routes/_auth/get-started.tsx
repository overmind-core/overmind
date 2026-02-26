import { createFileRoute } from "@tanstack/react-router";

import { QuickstartSnippets } from "@/components/quickstart/quickstart-snippets";

export const Route = createFileRoute("/_auth/get-started")({
  component: GetStartedPage,
});

function GetStartedPage() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 py-6">
      <QuickstartSnippets />
    </div>
  );
}
