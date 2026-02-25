import { useEffect, useRef } from "react";

import { useMutation } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { z } from "zod";

import apiClient from "@/client";

const querySchema = z.object({
  code: z.string(),
  state: z.string(),
});

export const Route = createFileRoute("/oauth-google-complete")({
  component: OAuthGoogleComplete,
  validateSearch: querySchema,
});

function OAuthGoogleComplete() {
  const navigate = Route.useNavigate();
  const { code, state } = Route.useSearch();
  const hasStarted = useRef(false);
  const completeLogin = useMutation({
    mutationFn: () =>
      apiClient.oauth.oauthGoogleCompleteApiV1OauthGoogleCompleteGet({
        code,
        state,
      }),
    onError: () => {
      navigate({ to: "/" });
    },
    onSuccess: (response) => {
      setTimeout(() => {
        localStorage.setItem("token", response.accessToken);
        localStorage.setItem("auth_user", JSON.stringify(response.user));
        navigate({ to: "/onboarding" });
      }, 300);
    },
  });

  // biome-ignore lint/correctness/useExhaustiveDependencies: manual
  useEffect(() => {
    if (hasStarted.current) return;
    if (code && state) {
      hasStarted.current = true;
      completeLogin.mutate();
    } else {
      navigate({ to: "/" });
    }
  }, [code, state]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4">
      <Loader2 className="animate-spin text-muted-foreground" size={56} />
      <div className="text-lg font-semibold text-foreground">Completing sign in...</div>
    </div>
  );
}
