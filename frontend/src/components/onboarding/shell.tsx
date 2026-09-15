import overmindEye from "@/assets/overmind-eye-copper.svg";
import { SplashBackground } from "@/components/splash-background";
import { Icon } from "@/components/ui/icons";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
export const SOLID_SECONDARY_BUTTON_CLASS =
  "border border-auth-border bg-auth-field text-auth-text hover:border-auth-border-hover hover:bg-auth-field-hover dark:bg-auth-field dark:hover:bg-auth-field-hover";

const PILLARS = [
  { description: "See everything your agent does", icon: Icon.observability, name: "Map & Trace" },
  {
    description: "Turn real behaviour into datasets",
    icon: Icon.dataset,
    name: "Curate & Prepare",
  },
  {
    description: "Catch regressions before your users do",
    icon: Icon.eval,
    name: "Evaluate & Optimise",
  },
  { description: "Own your intelligence", icon: Icon.training, name: "Train & Serve" },
] as const;

export function OnboardingShell({ children }: { children: React.ReactNode }) {
  return (
    <SplashBackground>
      <div className="flex min-h-full w-full items-center justify-center px-4 py-8 sm:px-6">
        <div className="grid h-[clamp(32rem,78vh,44rem)] w-full max-w-4xl animate-fade-in-up overflow-hidden rounded-md border-2 border-auth-border bg-auth-panel lg:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="hidden min-h-0 flex-col gap-6 overflow-y-auto border-r border-auth-border bg-auth-field/60 p-7 lg:flex">
            <img
              alt=""
              aria-hidden="true"
              className="size-12 shrink-0 object-contain"
              src={overmindEye}
            />
            <div>
              <h1 className={cn(TITLE.section, "text-auth-text")}>Welcome to Overmind</h1>
              <p className="mt-2 text-sm leading-relaxed text-auth-text-label">
                The model training platform for AI teams. Your production traces become specialised
                models you own.
              </p>
            </div>
            <ul className="mt-auto flex flex-col gap-4">
              {PILLARS.map(({ description, icon: PillarIcon, name }) => (
                <li className="flex items-start gap-2.5" key={name}>
                  <PillarIcon
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-auth-text-label"
                  />
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-sm font-semibold text-auth-text">{name}</span>
                    <span className="text-xs leading-relaxed text-auth-text-label">
                      {description}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </aside>

          <div className="flex min-h-0 flex-col">
            <div className="flex shrink-0 items-center gap-3 border-b border-auth-border px-6 py-4 lg:hidden">
              <img
                alt=""
                aria-hidden="true"
                className="size-8 shrink-0 object-contain"
                src={overmindEye}
              />
              <span className={cn(TITLE.card, "text-auth-text")}>Welcome to Overmind</span>
            </div>
            {children}
          </div>
        </div>
      </div>
    </SplashBackground>
  );
}
