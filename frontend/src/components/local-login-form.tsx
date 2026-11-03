import { useState } from "react";

import apiClient, { setTokens } from "@/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { getContext } from "@/integrations/tanstack-query";
import { ApiError } from "@/lib/api-error";
import { errorMessage } from "@/lib/notify";

export function LocalLoginForm({
  onSignedIn,
  recoveryEmail,
}: {
  onSignedIn: () => void;
  recoveryEmail?: string;
}) {
  const [email, setEmail] = useState(recoveryEmail ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await apiClient.auth.authLocalCreate({
        localSessionRequest: { email: email.trim(), password },
      });
      setTokens(res.access, res.refresh, res.user);
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
      aria-busy={submitting}
      className={
        recoveryEmail
          ? "flex w-full flex-col gap-4"
          : "flex w-full max-w-sm flex-col gap-4 rounded-md border border-auth-border bg-auth-panel/90 p-6"
      }
      onSubmit={(e) => void onSubmit(e)}
    >
      {!recoveryEmail && (
        <>
          <div className="flex flex-col gap-1">
            <h1 className="text-lg font-semibold text-auth-text">Continue</h1>
          </div>
          <p className="text-sm text-auth-text-label">Creates an account on first use.</p>
        </>
      )}
      <div className="flex flex-col gap-1.5">
        <Label className="text-auth-text-label" htmlFor="local-email">
          Email
        </Label>
        <Input
          autoComplete="email"
          className="border-auth-border bg-auth-field text-auth-text"
          id="local-email"
          onChange={(e) => setEmail(e.target.value)}
          readOnly={Boolean(recoveryEmail)}
          required
          type="email"
          value={email}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label className="text-auth-text-label" htmlFor="local-password">
          Password
        </Label>
        <Input
          autoComplete="current-password"
          autoFocus={Boolean(recoveryEmail)}
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
        <p className="py-2 text-sm text-muted-foreground" role="alert">
          {error}
        </p>
      ) : null}
      <Button className="w-full" disabled={submitting} size="lg" type="submit">
        {submitting ? <Spinner size="sm" /> : null}
        {submitting ? "Signing in" : recoveryEmail ? "Restore session" : "Continue"}
      </Button>
    </form>
  );
}
