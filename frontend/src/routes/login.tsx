import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/login")({
  component: RouteComponent,
});

import { ContinueWithGoogle } from "@/components/continue-with-google";
import { Separator } from "@/components/ui/separator";

const TAGLINE = "Supervision for Super-intelligence";
const BANNER_SRC = "/overmind_banner.jpg";

function RouteComponent() {
  const { authUser: user } = Route.useRouteContext();

  return (
    <div className="relative flex h-screen w-full flex-col bg-muted/30 lg:flex-row">
      {/* Subtle grid background - desktop only */}
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

      {/* Mobile: full-screen image with backdrop overlay */}
      <div className="absolute inset-0 lg:hidden">
        <img alt="Overmind" className="h-full w-full object-cover object-center" src={BANNER_SRC} />
        <div className="absolute inset-0 bg-linear-to-b from-black/30 via-black/50 to-black/80" />
      </div>

      {/* Form side - on mobile: overlay on image with backdrop; on desktop: side panel */}
      <div className="relative z-10 flex min-h-0 flex-1 flex-col justify-center px-6 py-8 sm:px-12 sm:py-12 lg:w-[420px] lg:shrink-0 lg:bg-transparent">
        <div className="space-y-4 rounded-xl bg-background/5 px-6 py-6 shadow-2xl backdrop-blur-md lg:rounded-none lg:bg-transparent lg:px-0 lg:py-0 lg:shadow-none lg:backdrop-blur-none">
          <h1 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl invert lg:invert-0">
            Welcome to Overmind
          </h1>
          <p className="text-center text-lg text-muted-foreground sm:text-xl">{TAGLINE}</p>
          <p className="text-center text-sm text-muted-foreground">
            Sign in with your Google account to get started
          </p>
          <div className="flex justify-center">
            <div className="w-full max-w-xs">
              <ContinueWithGoogle />
            </div>
          </div>
          {user && (
            <>
              <Separator className="my-4" />
              <div className="rounded-lg bg-muted/50 px-4 py-3 text-center text-sm">
                <p className="font-medium text-foreground">{user.name ?? user.email ?? user.id}</p>
                {user.email && <p className="text-muted-foreground">{user.email}</p>}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Banner: Desktop - full side panel */}
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
