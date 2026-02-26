import { useState } from "react";

import { SignIn, useUser } from "@clerk/clerk-react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";

import apiClient from "@/client";
import { Separator } from "@/components/ui/separator";

export const Route = createFileRoute("/login")({
  component: RouteComponent,
});

const FONT_LABEL: React.CSSProperties = { fontFamily: '"Pixelify Sans", monospace' };

function RouteComponent() {
  const { config } = Route.useRouteContext();

  return (
    <div className="fixed inset-0 bg-black">
      <img
        alt=""
        className="pointer-events-none absolute inset-0 h-full w-full object-cover"
        src="/A2.png"
      />
      <img
        alt=""
        className="pointer-events-none absolute inset-0 h-full w-full object-cover mix-blend-screen"
        src="/A1.png"
      />
      <div className="relative z-10 flex h-full items-center justify-center">
        <div className="w-[470px] rounded-lg border border-[#2E2A27] bg-[#1C1917]/90 px-10 py-10 backdrop-blur-sm">
          <h1
            className="mb-1 text-center text-[2.5rem] leading-tight text-white"
            style={{ fontFamily: '"PP Mondwest", Georgia, serif' }}
          >
            Sign in to Overmind
          </h1>
          <p
            className="text-center text-[30px] text-[#6B6560]"
            style={{ fontFamily: '"NeueBit", monospace' }}
          >
            Welcome back
          </p>
          {config.isSelfHosted ? <OSSAuth /> : <EEAuth />}
        </div>
      </div>
    </div>
  );
}

const OSSAuth = () => {
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    setError(null);
    setLoading(true);
    try {
      const res = await apiClient.users.loginApiV1IamUsersLoginPost({
        loginRequest: { email, password },
      });
      localStorage.setItem("token", res.accessToken);
      navigate({ to: "/" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleLogin();
  };
  return (
    <div className="space-y-3">
      <div>
        <label
          className="mb-1.5 block text-[15px] font-semibold uppercase tracking-wider text-[#8A8580]"
          htmlFor="login-email"
          style={FONT_LABEL}
        >
          Username *
        </label>
        <input
          className="h-11 w-full rounded-md border border-[#2E2A27] bg-[#252220] px-3.5 text-sm text-white placeholder-[#5A5550] outline-none transition-colors focus:border-[#C8956A]"
          id="login-email"
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="admin"
          type="text"
          value={email}
        />
      </div>

      <div>
        <label
          className="mb-1.5 block text-[15px] font-semibold uppercase tracking-wider text-[#8A8580]"
          htmlFor="login-password"
          style={FONT_LABEL}
        >
          Password *
        </label>
        <input
          className="h-11 w-full rounded-md border border-[#2E2A27] bg-[#252220] px-3.5 text-sm text-white placeholder-[#5A5550] outline-none transition-colors focus:border-[#C8956A]"
          id="login-password"
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="admin"
          type="password"
          value={password}
        />
      </div>

      {error && <p className="text-center text-sm text-red-400">{error}</p>}

      <button
        className="flex h-11 w-full items-center justify-center rounded-md border border-[#3A3530] bg-[#252220] text-sm font-medium text-white transition-colors hover:border-[#4A4540] hover:bg-[#302E2C] disabled:opacity-50"
        disabled={loading}
        onClick={handleLogin}
        type="button"
      >
        {loading ? <Loader2 className="size-4 animate-spin" /> : "Sign In"}
      </button>
    </div>
  );
};

const EEAuth = () => {
  const { user } = useUser();
  return (
    <>
      <SignIn
        appearance={{
          elements: {
            card: "bg-transparent border-none pt-0",
            cardBox: "bg-transparent border-none",
            headerTitle: "hidden",
            logoBox: "hidden",
          },
        }}
        fallbackRedirectUrl={null}
        oauthFlow={"popup"}
        withSignUp
      />
      {user && (
        <>
          <Separator className="my-4" />
          <div className="rounded-lg bg-muted/50 px-4 py-3 text-center text-sm">
            <p className="font-medium text-foreground">
              {user.fullName ?? user.emailAddresses[0].emailAddress ?? user.id}
            </p>
            {user.emailAddresses[0].emailAddress && (
              <p className="text-muted-foreground">{user.emailAddresses[0].emailAddress}</p>
            )}
          </div>
        </>
      )}
    </>
  );
};
