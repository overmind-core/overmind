import { cn } from "@/lib/utils";

/**
 * The art is authored light-on-dark, so light theme inverts both layers and
 * swaps the starfield's `screen` blend for its dual, `multiply`.
 * Needs a `relative overflow-hidden` parent.
 */
function SplashArt({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cn("pointer-events-none absolute inset-0", className)}>
      <img
        alt=""
        className="absolute inset-0 h-full w-full animate-fade-in object-cover invert dark:invert-0"
        src="/A2.png"
      />
      <img
        alt=""
        className="absolute inset-0 h-full w-full animate-fade-in object-cover invert mix-blend-multiply [animation-delay:200ms] dark:invert-0 dark:mix-blend-screen"
        src="/A1.png"
      />
    </div>
  );
}

export function SplashBackground({ children }: { children?: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-auth-splash">
      <SplashArt />
      <div className="relative z-10 flex h-full items-center justify-center overflow-y-auto">
        {children}
      </div>
    </div>
  );
}
