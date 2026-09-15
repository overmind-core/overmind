import { useNavigate } from "@tanstack/react-router";

import { OnboardingShell, SOLID_SECONDARY_BUTTON_CLASS } from "@/components/onboarding/shell";
import { Button } from "@/components/ui/button";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

export function GuestRepoPicker() {
  const navigate = useNavigate();

  return (
    <OnboardingShell>
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-7 pb-5 pt-6">
          <div className="shrink-0">
            <div className="flex items-center gap-2">
              <h2 className={cn(TITLE.card, "text-auth-text")}>Set up locally</h2>
              <span className="chip-label inline-flex shrink-0 items-center rounded-sm border border-auth-border bg-auth-field px-1.5 py-0.5 text-xs text-auth-text-label">
                DEMO
              </span>
            </div>
            <p className="mt-1 text-sm text-auth-text-label">
              Capability discovery runs in your repo. Create an account, then run{" "}
              <span className="font-mono text-auth-text">overmind init</span>,{" "}
              <span className="font-mono text-auth-text">/overmind setup</span>, and{" "}
              <span className="font-mono text-auth-text">overmind sync</span>.
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-3 border-t border-auth-border px-7 py-4">
          <Button
            className={SOLID_SECONDARY_BUTTON_CLASS}
            onClick={() => void navigate({ search: { mode: "signup" }, to: "/login" })}
            type="button"
            variant="secondary"
          >
            Create an account
          </Button>
        </div>
      </div>
    </OnboardingShell>
  );
}
