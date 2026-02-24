import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/login")({
  component: RouteComponent,
});

import { ContinueWithGoogle } from "@/components/continue-with-google";

function RouteComponent() {
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
            className="mb-8 text-center text-[30px] text-[#6B6560]"
            style={{ fontFamily: '"NeueBit", monospace' }}
          >
            Welcome back
          </p>

          <ContinueWithGoogle />
        </div>
      </div>
    </div>
  );
}
