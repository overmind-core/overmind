import { useCallback, useEffect, useRef, useState } from "react";

import apiClient, { setTokens } from "@/client";
import { getProviderIcon, ProviderLogo } from "@/components/model-provider-chip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { getContext } from "@/integrations/tanstack-query";
import { ApiError } from "@/lib/api-error";
import { errorMessage } from "@/lib/notify";
import type { ChatGPTLogin } from "@/openapi";

export function LocalLoginForm({
  onSignedIn,
  chatgptComplete = false,
  chatgptStart = false,
}: {
  onSignedIn: () => void;
  chatgptComplete?: boolean;
  chatgptStart?: boolean;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [linkEmail, setLinkEmail] = useState<string | null>(null);
  const [chatgpt, setChatGPT] = useState<ChatGPTLogin>();
  const [connecting, setConnecting] = useState(chatgptComplete || chatgptStart);
  const completing = useRef(false);
  const starting = useRef(false);
  const autoStarted = useRef(false);
  const pending = submitting || connecting;

  useEffect(() => {
    void apiClient.chatgpt
      .chatgptLoginRetrieve({ credentials: "include" })
      .then(setChatGPT)
      .catch(() => {
        // Password sign-in remains available if the optional provider is unavailable.
      });
  }, []);

  useEffect(() => {
    if (!chatgptComplete || completing.current) return;
    completing.current = true;
    void apiClient.chatgpt
      .chatgptCallbackSessionRetrieve({ credentials: "include" })
      .then(async (handoff) => {
        if (handoff.requiresPassword) {
          setLinkEmail(handoff.email);
          return;
        }
        const session = await apiClient.chatgpt.chatgptCallbackSessionCreate(
          { chatGPTLoginConfirmRequest: {} },
          { credentials: "include" }
        );
        setTokens(session.access, session.refresh);
        void getContext().queryClient.invalidateQueries();
        onSignedIn();
      })
      .catch((err) => setError(errorMessage(err, "Could not complete ChatGPT sign-in.")))
      .finally(() => setConnecting(false));
  }, [chatgptComplete, onSignedIn]);

  const continueWithChatGPT = useCallback(
    async (useAnother = false) => {
      if (starting.current) return;
      starting.current = true;
      setConnecting(true);
      setError(null);
      if (window.location.hostname === "localhost") {
        const loopback = new URL(window.location.href);
        loopback.hostname = "127.0.0.1";
        loopback.searchParams.set("chatgpt", "start");
        window.location.assign(loopback.href);
        return;
      }
      try {
        const result = await apiClient.chatgpt.chatgptLoginCreate(
          { chatGPTLoginRequestRequest: { email: email.trim(), useAnother } },
          { credentials: "include" }
        );
        window.location.assign(result.authorizationUrl);
      } catch (err) {
        setError(errorMessage(err, "Could not start ChatGPT sign-in."));
        setConnecting(false);
        starting.current = false;
      }
    },
    [email]
  );

  useEffect(() => {
    if (!chatgptStart || autoStarted.current) return;
    autoStarted.current = true;
    void continueWithChatGPT();
  }, [chatgptStart, continueWithChatGPT]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = linkEmail
        ? await apiClient.chatgpt.chatgptCallbackSessionCreate(
            { chatGPTLoginConfirmRequest: { password } },
            { credentials: "include" }
          )
        : await apiClient.auth.authLocalCreate({
            localSessionRequest: { email: email.trim(), password },
          });
      setTokens(res.access, res.refresh);
      void getContext().queryClient.invalidateQueries();
      onSignedIn();
    } catch (err) {
      setError(
        err instanceof ApiError && err.hasDetail
          ? err.message
          : errorMessage(err, "Could not sign in.")
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      className="animate-fade-in-up flex w-full max-w-sm flex-col gap-4 rounded-md border border-auth-border bg-auth-panel/90 p-6 [animation-delay:400ms]"
      onSubmit={(e) => void onSubmit(e)}
    >
      <div className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold text-auth-text">
          {linkEmail ? "Connect your existing account" : "Continue"}
        </h1>
      </div>
      {chatgpt?.enabled && !linkEmail ? (
        <>
          <div className="flex flex-col gap-2">
            <Button
              aria-label="Continue with ChatGPT"
              className="w-full"
              disabled={pending}
              onClick={() => void continueWithChatGPT()}
              size="lg"
              type="button"
            >
              {connecting ? (
                <Spinner size="sm" />
              ) : (
                <ProviderLogo
                  Icon={getProviderIcon("openai")}
                  providerLabel="OpenAI"
                  providerSlug="openai"
                />
              )}
              Continue with ChatGPT
            </Button>
            {chatgpt.rememberedEmail ? (
              <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs text-auth-text-label">
                <span className="break-all">{chatgpt.rememberedEmail}</span>
                <Button
                  disabled={pending}
                  onClick={() => void continueWithChatGPT(true)}
                  size="xs"
                  type="button"
                  variant="link"
                >
                  Use another account
                </Button>
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-3 text-xs text-auth-text-label">
            <div className="h-px flex-1 bg-auth-border" />
            <span>or continue with email</span>
            <div className="h-px flex-1 bg-auth-border" />
          </div>
        </>
      ) : null}
      <p className="text-sm text-auth-text-label">
        {linkEmail
          ? `Signed in to ChatGPT as ${linkEmail}. Confirm your Overmind password once to link this account.`
          : "Creates an account on first use."}
      </p>
      {!linkEmail ? (
        <div className="flex flex-col gap-1.5">
          <Label className="text-auth-text-label" htmlFor="local-email">
            Email
          </Label>
          <Input
            autoComplete="email"
            className="border-auth-border bg-auth-field text-auth-text"
            id="local-email"
            onChange={(e) => setEmail(e.target.value)}
            required
            type="email"
            value={email}
          />
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label className="text-auth-text-label" htmlFor="local-password">
          {linkEmail ? "Overmind password" : "Password"}
        </Label>
        <Input
          autoComplete="current-password"
          className="border-auth-border bg-auth-field text-auth-text"
          id="local-password"
          minLength={8}
          onChange={(e) => setPassword(e.target.value)}
          required
          type="password"
          value={password}
        />
      </div>
      {error ? (
        <p
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          role="alert"
        >
          {error}
        </p>
      ) : null}
      <Button
        className="w-full"
        disabled={pending}
        size="lg"
        type="submit"
        variant={chatgpt?.enabled ? "secondary" : "default"}
      >
        {submitting ? <Spinner size="sm" /> : null}
        {linkEmail ? "Connect and sign in" : "Continue"}
      </Button>
      {linkEmail ? (
        <Button asChild size="sm" variant="link">
          <a href="/login">Start again</a>
        </Button>
      ) : null}
    </form>
  );
}
