import { useEffect, useRef } from "react";

import { useMutation } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Loader as Loader2 } from "pixelarticons/react";
import { z } from "zod";

import apiClient from "@/client";
import { useAuthContext } from "@/contexts/auth-context";

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
  const { refreshAuth } = useAuthContext();
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
        refreshAuth?.();
        navigate({ to: "/onboarding" });
      }, 300);
    },
  });

  useEffect(() => {
    if (hasStarted.current) return;
    if (code && state) {
      hasStarted.current = true;
      completeLogin.mutate();
    } else {
      navigate({ to: "/" });
    }
  }, [code, state, completeLogin, navigate]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4">
      <Loader2 className="size-14 animate-spin text-muted-foreground" />
      <div className="text-lg font-semibold text-foreground">Completing sign in...</div>
    </div>
  );
}
