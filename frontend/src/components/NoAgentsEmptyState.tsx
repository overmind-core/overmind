import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Key } from "lucide-react";

import { CreateApiKeyDialog } from "@/components/CreateApiKeyDialog";
import { useProjectsList } from "@/hooks/use-projects";

export function NoAgentsEmptyState() {
  const queryClient = useQueryClient();
  const { data: projectsData } = useProjectsList();
  const project = projectsData?.projects?.[0];
  const [showCreateKey, setShowCreateKey] = useState(false);

  return (
    <div className="flex w-full flex-col items-center py-8 text-center">
      <p className="mb-2 font-display text-4xl font-medium">No agents detected yet</p>
      <p className="mx-auto mb-4 max-w-sm text-sm text-muted-foreground">
        Connect your LLM application and ingest traces, then extract templates to see your agents
        here.
      </p>
      <div className="flex flex-wrap justify-center gap-3">
        <a
          className="inline-flex items-center rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white hover:bg-black/80 dark:bg-white dark:text-black dark:hover:bg-white/80"
          href="https://docs.overmindlab.ai/"
          rel="noopener noreferrer"
          target="_blank"
        >
          Integration Guide <ExternalLink className="ml-1.5 size-4" />
        </a>
        {project && (
          <>
            <button
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
              onClick={() => setShowCreateKey(true)}
              type="button"
            >
              <Key className="size-4" />
              Create API Key
            </button>
            <CreateApiKeyDialog
              onCreated={() => queryClient.invalidateQueries({ queryKey: ["tokens", project.projectId] })}
              onOpenChange={setShowCreateKey}
              open={showCreateKey}
              organisationId={project.organisationId}
              projectId={project.projectId}
            />
          </>
        )}
      </div>
    </div>
  );
}
