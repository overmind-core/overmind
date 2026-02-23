import { useState } from "react";

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/login")({
  component: RouteComponent,
});

const TAGLINE = "Supervision for Super-intelligence";
const BANNER_SRC = "/overmind_banner.jpg";

function RouteComponent() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${baseUrl}/api/v1/iam/users/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail?.message ?? data?.detail ?? "Invalid email or password");
      }
      const data = await res.json();
      localStorage.setItem("token", data.access_token);
      localStorage.setItem(
        "auth_user",
        JSON.stringify({
          id: data.user.user_id,
          email: data.user.email,
          name: data.user.full_name,
          picture: null,
        })
      );
      navigate({ to: "/" });
    } catch (err) {
      setError((err as Error).message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex h-screen w-full flex-col bg-muted/30 lg:flex-row">
      <div
        className="pointer-events-none absolute inset-0 z-0 hidden lg:block"
        style={{
          backgroundImage: `
            repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(0,0,0,0.02) 39px, rgba(0,0,0,0.02) 40px),
            repeating-linear-gradient(90deg, transparent, transparent 39px, rgba(0,0,0,0.02) 39px, rgba(0,0,0,0.02) 40px)
          `,
          backgroundSize: "40px 40px",
        }}
      />

      <div className="absolute inset-0 lg:hidden">
        <img alt="Overmind" className="h-full w-full object-cover object-center" src={BANNER_SRC} />
        <div className="absolute inset-0 bg-linear-to-b from-black/30 via-black/50 to-black/80" />
      </div>

      <div className="relative z-10 flex min-h-0 flex-1 flex-col justify-center px-6 py-8 sm:px-12 sm:py-12 lg:w-[420px] lg:shrink-0 lg:bg-transparent">
        <div className="space-y-5 rounded-xl bg-background/5 px-6 py-6 shadow-2xl backdrop-blur-md lg:rounded-none lg:bg-transparent lg:px-0 lg:py-0 lg:shadow-none lg:backdrop-blur-none">
          <h1 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl invert lg:invert-0">
            Welcome to Overmind
          </h1>
          <p className="text-center text-lg text-muted-foreground sm:text-xl">{TAGLINE}</p>

          <form className="mx-auto w-full max-w-xs space-y-4" onSubmit={handleLogin}>
            <div className="space-y-1.5">
              <Label className="invert lg:invert-0" htmlFor="email">
                Username
              </Label>
              <Input
                autoComplete="username"
                id="email"
                onChange={(e) => setEmail(e.target.value)}
                placeholder="admin"
                required
                type="text"
                value={email}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="invert lg:invert-0" htmlFor="password">
                Password
              </Label>
              <Input
                autoComplete="current-password"
                id="password"
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                required
                type="password"
                value={password}
              />
            </div>
            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <Button className="w-full h-11 font-medium" disabled={loading} size="lg" type="submit">
              {loading && <Loader2 className="mr-2 size-4 animate-spin" />}
              Sign in
            </Button>
          </form>

          <p className="text-center text-xs text-muted-foreground">
            Default credentials: admin / admin
          </p>
        </div>
      </div>

      <div className="relative hidden min-h-0 flex-1 lg:block">
        <img
          alt="Overmind"
          className="absolute inset-0 h-full w-full object-cover object-center"
          fetchPriority="high"
          loading="eager"
          src={BANNER_SRC}
        />
        <div className="absolute inset-0 bg-linear-to-r from-background/80 via-background/40 to-transparent" />
        <div className="relative flex h-full flex-col justify-end p-8 text-black dark:text-white">
          <p className="max-w-sm text-lg font-medium drop-shadow-sm lg:text-xl">{TAGLINE}</p>
        </div>
      </div>
    </div>
  );
}
