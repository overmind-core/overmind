import desktopMonitorIcon from "@/assets/desktop-monitor.svg";
import overmindEye from "@/assets/overmind-eye-copper.svg";
import { SplashBackground } from "@/components/splash-background";
import { LABEL, PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

/** Full-screen gate when the authenticated console is opened on a phone. */
export function DesktopRequired() {
  return (
    <SplashBackground>
      <main className="flex w-full max-w-sm flex-col items-center px-6 py-10 text-center">
        <DesktopRequiredGraphic />
        <p
          className={cn(
            LABEL.pixel,
            "mt-8 animate-fade-in-up text-auth-text-label [animation-delay:120ms]"
          )}
        >
          Overmind Console
        </p>
        <h1
          className={cn(
            TITLE.hero,
            "mt-2 animate-fade-in-up text-balance text-auth-text [animation-delay:200ms]"
          )}
        >
          Desktop required
        </h1>
        <p
          className={cn(
            PROSE,
            "mt-2 max-w-[22rem] animate-fade-in-up text-sm text-auth-text-label [animation-delay:280ms]"
          )}
        >
          Open Overmind in a desktop browser.
        </p>
      </main>
    </SplashBackground>
  );
}

function DesktopRequiredGraphic() {
  return (
    <div
      aria-hidden="true"
      className="relative w-[min(17.5rem,78vw)] animate-fade-in-up [animation-delay:40ms]"
    >
      <div className="rounded-md border-2 border-auth-border bg-auth-panel p-2">
        <div className="relative aspect-[5/4] overflow-hidden rounded-sm border border-auth-border bg-auth-splash">
          <img
            alt=""
            className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-20"
            src="/console-noise.svg"
          />
          {/* Faux console chrome — sidebar + content rails */}
          <div className="absolute inset-x-3 top-3 flex gap-2 opacity-45">
            <div className="h-1.5 w-8 rounded-xs bg-auth-text/50" />
            <div className="h-1.5 flex-1 rounded-xs bg-auth-text/25" />
          </div>
          <div className="absolute bottom-3 left-3 top-8 w-7 rounded-xs border border-auth-text/20 bg-auth-text/5" />
          <div className="absolute bottom-3 left-12 right-3 top-8 space-y-1.5 opacity-40">
            <div className="h-1.5 w-[72%] rounded-xs bg-auth-text/40" />
            <div className="h-1.5 w-full rounded-xs bg-auth-text/20" />
            <div className="h-1.5 w-[58%] rounded-xs bg-auth-text/20" />
            <div className="mt-3 grid grid-cols-3 gap-1.5">
              <div className="aspect-square rounded-xs border border-auth-text/20 bg-auth-text/5" />
              <div className="aspect-square rounded-xs border border-auth-text/20 bg-auth-text/5" />
              <div className="aspect-square rounded-xs border border-auth-text/20 bg-auth-text/5" />
            </div>
          </div>
          <img
            alt=""
            className="absolute left-1/2 top-[46%] size-14 -translate-x-1/2 -translate-y-1/2 object-contain"
            src={overmindEye}
          />
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="desktop-required-scan absolute inset-x-0 h-8 bg-auth-text/10" />
          </div>
        </div>
      </div>
      <div className="mx-auto h-3 w-10 border-x-2 border-auth-border bg-auth-field" />
      <div className="mx-auto h-2 w-24 rounded-sm border-2 border-auth-border bg-auth-panel" />
      <img
        alt=""
        className="absolute -right-1 -top-1 size-9 [image-rendering:pixelated] dark:invert"
        src={desktopMonitorIcon}
      />
    </div>
  );
}
