import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { Bot, Loader2, RefreshCw } from "lucide-react";

import { ResponseError } from "@/api";
import apiClient from "@/client";
import { AgentGrid } from "@/components/agent-grid";
import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { Alert } from "@/components/ui/alert";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const Route = createFileRoute("/_auth/")({
  component: HomePage,
});


function AgentsSection() {
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryFn: () => apiClient.agents.listAgentsApiV1AgentsGet(),
    queryKey: ["agents"],
    refetchInterval: 15_000,
  });

  const extractMutation = useMutation({
    mutationFn: () =>
      apiClient.jobs
        .createTemplateExtractionApiV1JobsExtractTemplatesPost()
        .catch(async (error) => {
          if (error instanceof ResponseError) {
            const r = await error.response.json();
            throw new Error(r.detail ?? "Extraction failed");
          }
          throw error;
        }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });

  const agents = data?.data ?? [];

  if (isLoading) {
    return (
      <Card className="mb-6 flex min-h-[200px] items-center justify-center">
        <CardContent className="flex flex-col items-center gap-3 py-8">
          <Loader2 className="size-9 animate-spin text-amber-600" />
          <p className="text-sm text-muted-foreground">Loading agents…</p>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="mb-6">
        <CardContent className="p-6">
          <Alert variant="destructive">Failed to load agents: {(error as Error).message}</Alert>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="mb-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Bot className="size-5 shrink-0 text-black dark:text-white" />
          <h2 className="font-display text-lg font-bold text-black dark:text-white">
            Detected Agents
          </h2>
          {agents.length > 0 && (
            <span className="rounded-sm bg-black px-2 py-0.5 text-sm font-semibold text-white dark:bg-white dark:text-black">
              {agents.length}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {agents.length > 0 && (
            <Button asChild size="sm" variant="ghost">
              <Link to="/agents">View all</Link>
            </Button>
          )}
          <Button
            disabled={extractMutation.isPending}
            onClick={() => extractMutation.mutate()}
            size="sm"
            variant="outline"
          >
            {extractMutation.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <RefreshCw className="size-3.5" />
            )}
            {extractMutation.isPending ? "Extracting…" : "Extract Templates"}
          </Button>
        </div>
      </div>
      <DismissibleAlert
        error={extractMutation.isError ? (extractMutation.error as Error) : null}
        fallback="Extraction failed"
        variant="warning"
      />
      {extractMutation.isSuccess && (
        <Alert variant="success">Template extraction started — results will appear shortly.</Alert>
      )}
      {agents.length === 0 ? <QuickstartEmbed /> : <AgentGrid agents={agents} />}
    </div>
  );
}

function HomePage() {
  return (
    <div className="space-y-6 pb-8">
      <AgentsSection />
    </div>
  );
}
