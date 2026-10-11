import { useEffect } from "react";

import { useMutation, useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import api from "@/client";
import { ConnectionPanel, ConnectionScene, connectionHeading } from "@/components/connection-scene";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Spinner } from "@/components/ui/spinner";
import { useAuthContext } from "@/contexts/auth-context";

export const Route = createFileRoute("/oauth/authorize")({
  component: AuthorizeConnection,
  validateSearch: z.object({ request: z.string().optional() }),
});

function AuthorizeConnection() {
  const { request } = Route.useSearch();
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  const ready = isLoaded && isSignedIn && !isGuest;
  const details = useQuery({
    enabled: ready && Boolean(request),
    gcTime: 0,
    queryFn: () => api.mcpOauth.mcpOauthConsentRetrieve({ request: request ?? "" }),
    queryKey: ["mcp-consent", request],
    retry: false,
  });
  const decision = useMutation({
    mutationFn: (approve: boolean) =>
      api.mcpOauth.mcpOauthConsentCreate({
        consentDecisionRequest: { approve, request: request ?? "" },
      }),
    onSuccess: (result) => window.location.assign(result.redirectUrl),
  });

  useEffect(() => {
    if (!isLoaded || ready) return;
    const next = window.location.pathname + window.location.search;
    window.location.replace(`/login?next=${encodeURIComponent(next)}`);
  }, [isLoaded, ready]);

  return (
    <ConnectionScene>
      <div className="flex w-full max-w-xl flex-col items-center gap-5">
        <img alt="Overmind" className="size-10" height={40} src="/favicon.png" width={40} />
        <PageHeader className={connectionHeading} title="Connect to Overmind" />
      </div>
      <ConnectionPanel>
        {!request ? <Alert variant="destructive">Missing connection request.</Alert> : null}
        {request && (!ready || details.isPending) ? <Spinner /> : null}
        {details.isError ? (
          <Alert variant="destructive">
            This request is unavailable. Start the connection again from your MCP client.
          </Alert>
        ) : null}
        {details.data ? (
          <>
            <div className="space-y-2 text-sm leading-relaxed">
              <p className="break-words text-base">{details.data.clientName}</p>
              <p className="text-muted-foreground">Requests access to your Overmind account.</p>
            </div>
            <div className="space-y-4 text-sm leading-relaxed">
              <p>All projects you can access, including projects added later.</p>
              <ul className="list-disc space-y-3 pl-5 marker:text-muted-foreground">
                {details.data.scopes.includes("overmind:read") ? (
                  <li>Read traces, datasets, evaluations, training and serving status.</li>
                ) : null}
                {details.data.scopes.includes("overmind:write") ? (
                  <li>
                    Create and change data, run evaluations and training, and manage serving.
                    <span className="block text-muted-foreground">
                      These actions can incur costs.
                    </span>
                  </li>
                ) : null}
              </ul>
              <p className="text-muted-foreground">Access continues until revoked.</p>
            </div>
            <div className="-mx-5 space-y-1 border-t border-border px-5 pt-4 text-xs leading-relaxed sm:-mx-7 sm:px-7">
              <p className="text-muted-foreground">Return address</p>
              <p className="break-all">{details.data.redirectUri}</p>
            </div>
            {decision.isError ? (
              <Alert variant="destructive">
                The connection could not be completed. Start again from your MCP client.
              </Alert>
            ) : null}
            <div className="grid grid-cols-2 gap-3">
              <Button
                disabled={decision.isPending}
                onClick={() => decision.mutate(false)}
                size="lg"
                variant="secondary"
              >
                Cancel
              </Button>
              <Button disabled={decision.isPending} onClick={() => decision.mutate(true)} size="lg">
                {decision.isPending ? <Spinner /> : null}Connect
              </Button>
            </div>
          </>
        ) : null}
      </ConnectionPanel>
    </ConnectionScene>
  );
}
