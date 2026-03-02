import { useMemo, useState } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader as Loader2 } from "pixelarticons/react";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { config } from "@/config";
import { useProjectsList } from "@/hooks/use-projects";
import { cn } from "@/lib/utils";

const quickstartCodeSnippet = (apiKey: string) => `import os
from overmind.clients import OpenAI
os.environ["OVERMIND_API_KEY"] = "${apiKey}"
`;

const quickstartCodeSnippetPython = `openai_client = OpenAI(...)

response = openai_client.chat.completions.create(
    model="gpt-5-mini",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
)
print(response.choices[0].message.content)
`;

function CodeBlock({ children }: { children: React.ReactNode }) {
  return (
    <pre className="my-1 max-w-full overflow-x-auto rounded border border-border bg-muted/50 p-3 font-mono text-sm text-muted-foreground whitespace-pre-wrap break-words">
      {children}
    </pre>
  );
}

export function APIKeySection({
  short = false,
  fillHeight = false,
  projectId,
}: {
  short?: boolean;
  fillHeight?: boolean;
  projectId?: string;
}) {
  const [showCopied, setShowCopied] = useState(false);
  const { data: projectsData, isLoading: isLoading } = useProjectsList();

  const currentProject = useMemo(() => {
    return projectId
      ? projectsData?.projects?.find((p) => p.projectId === projectId)
      : projectsData?.projects?.[0];
  }, [projectsData, projectId]);

  const { data: initialApiKey, refetch: refetchKey } = useQuery({
    enabled: !!currentProject,
    queryFn: () => {
      if (!currentProject) throw new Error("No project found");
      const telemetryApiKey = localStorage.getItem(`telemetry_api_key_${currentProject.projectId}`);
      return telemetryApiKey ? Promise.resolve(telemetryApiKey) : Promise.resolve(null);
    },
    queryKey: ["telemetry_api_key", currentProject?.projectId],
  });

  const generateApiKey = useMutation({
    mutationFn: async () => {
      if (!currentProject) throw new Error("No project found");
      const tokenResponse = await apiClient.tokens.createTokenApiV1IamTokensPost({
        createTokenRequest: {
          description: "API key for telemetry",
          name: `Telemetry API Key - ${new Date().toISOString()}`,
          organisationId: currentProject.organisationId,
          projectId: currentProject.projectId,
        },
      });

      if (!config.isSelfHosted) {
        const rolesRes = await apiClient.roles.listCoreRolesApiV1IamRolesGet({
          scope: "project",
        });
        const adminRole = rolesRes.roles?.find((r) => r.name === "project_admin");
        if (!adminRole) throw new Error("Admin role not found");
        await apiClient.tokenRoles.assignTokenRoleApiV1IamTokensTokenIdRolesPost({
          assignTokenRoleRequest: {
            expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 300),
            roleId: adminRole.roleId,
            scopeId: currentProject.projectId,
            scopeType: "project",
          },
          tokenId: tokenResponse.tokenId,
        });
      }

      localStorage.setItem(`telemetry_api_key_${currentProject.projectId}`, tokenResponse.token);
      return tokenResponse;
    },
    onSuccess: () => refetchKey(),
  });

  const handleCopy = async () => {
    if (!initialApiKey) return;
    await navigator.clipboard.writeText(quickstartCodeSnippet(initialApiKey));
    setShowCopied(true);
    setTimeout(() => setShowCopied(false), 1300);
  };

  if (isLoading) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-md border border-border bg-card p-8",
          fillHeight && "min-h-full"
        )}
      >
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col rounded-md border border-border bg-card p-6",
        fillHeight && "min-h-full"
      )}
    >
      {initialApiKey && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span className="text-lg font-bold text-amber-600">
            Drop in three lines for instant tracing
          </span>
          <Button
            className={showCopied ? "border-green-500 bg-green-600" : ""}
            onClick={handleCopy}
            size="sm"
            variant={showCopied ? "default" : "outline"}
          >
            {showCopied ? "Copied!" : "Copy"}
          </Button>
        </div>
      )}

      {initialApiKey ? (
        <div>
          <CodeBlock>{quickstartCodeSnippet(initialApiKey)}</CodeBlock>
          {!short && (
            <>
              <p className="mt-4 mb-2 text-sm text-muted-foreground">
                2. Use your LLM client as normal.
              </p>
              <CodeBlock>{quickstartCodeSnippetPython}</CodeBlock>
            </>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center py-6">
          <p className="mb-4 text-muted-foreground">You don&apos;t have an API key yet.</p>
          <Button
            disabled={generateApiKey.isPending || projectsData?.projects?.length === 0}
            onClick={() => generateApiKey.mutate()}
          >
            {generateApiKey.isPending ? <Loader2 className="mr-2 size-5 animate-spin" /> : null}
            Generate API Key
          </Button>
        </div>
      )}
    </div>
  );
}
