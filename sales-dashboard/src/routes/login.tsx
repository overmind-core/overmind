import { type FormEvent, useState } from "react";

import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";

import overwatchLogo from "~/assets/overwatch-logo.svg";
import { getToken, overwatchGet, setToken, Unauthorized } from "~/lib/api";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { TITLE } from "@/lib/typography";

export const Route = createFileRoute("/login")({
  beforeLoad: () => {
    if (getToken()) throw redirect({ search: { days: 30 }, to: "/" });
  },
  component: Login,
  validateSearch: (search: Record<string, unknown>): { rejected?: boolean } =>
    search.rejected ? { rejected: true } : {},
});

function Login() {
  const navigate = useNavigate();
  const { rejected } = Route.useSearch();
  const [token, setDraft] = useState("");
  const [error, setError] = useState(rejected ? "Token rejected" : "");
  const [checking, setChecking] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setChecking(true);
    setError("");
    try {
      await overwatchGet("activation", { days: 1 }, token.trim());
      setToken(token.trim());
      await navigate({ search: { days: 30 }, to: "/" });
    } catch (failure) {
      setError(failure instanceof Unauthorized ? "Token rejected" : "API unreachable");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm space-y-4 bg-wash-subtle p-6">
        <div className="flex items-center gap-2">
          <img alt="" className="size-6 shrink-0" height={24} src={overwatchLogo} width={24} />
          <h1 className={TITLE.card}>Overwatch</h1>
        </div>
        <form className="space-y-3" onSubmit={submit}>
          <label className="block space-y-1.5 text-sm" htmlFor="token">
            <span className="text-muted-foreground">Admin token</span>
            <Input
              autoComplete="off"
              autoFocus
              id="token"
              onChange={(event) => setDraft(event.target.value)}
              type="password"
              value={token}
            />
          </label>
          {error ? (
            <Alert variant="destructive">
              <Icon.warning />
              <span>{error}</span>
            </Alert>
          ) : null}
          <Button className="w-full" disabled={!token.trim() || checking} type="submit">
            {checking ? <Spinner className="text-primary-foreground" size="sm" /> : null}
            Continue
          </Button>
        </form>
      </Card>
    </div>
  );
}
