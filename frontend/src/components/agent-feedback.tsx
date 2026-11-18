import { useId } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Switch } from "@/components/ui/switch";
import { useAuthContext } from "@/contexts/auth-context";
import { useOnboardingStatus } from "@/hooks/use-query";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";

export const AGENT_FEEDBACK_DOCS = "https://docs.overmindlab.ai/reference/agent-feedback";

/**
 * What Connect saves: the choice the person made on the screen, or the ticked default when the
 * account's current value is still unset. An untouched box never overwrites a stored choice.
 */
export function choiceToRecord(touched: boolean | null, current: boolean | null | undefined) {
  if (touched !== null) return touched;
  return current === null ? true : null;
}

export function useAgentFeedbackChoice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (agentFeedback: boolean) =>
      apiClient.auth.authMePartialUpdate({ patchedUserPreferencesRequest: { agentFeedback } }),
    onError: (e) => notify.error(e, "Couldn't save your agent feedback choice"),
    onSuccess: (user) => client.setQueryData(["auth-me"], user),
  });
}

function DocsLink({ className }: { className?: string }) {
  return (
    <a
      className={cn("underline underline-offset-4 hover:text-foreground", className)}
      href={AGENT_FEEDBACK_DOCS}
      rel="noreferrer"
      target="_blank"
    >
      What is sent
    </a>
  );
}

/** The sign-up and connect screens: a choice the person sees before any agent reports. */
export function AgentFeedbackCheckbox({
  checked,
  onCheckedChange,
  tone = "console",
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  tone?: "auth" | "console";
}) {
  const id = useId();
  const muted = tone === "auth" ? "text-auth-text/70" : "text-muted-foreground";
  return (
    <div className="flex items-start gap-3 text-sm">
      <Checkbox
        checked={checked}
        className="mt-0.5"
        id={id}
        onCheckedChange={(value) => onCheckedChange(value === true)}
      />
      <div className="space-y-1">
        <label className={tone === "auth" ? "text-auth-text" : undefined} htmlFor={id}>
          Let my coding agents send feedback to Overmind
        </label>
        <p className={cn("text-xs leading-relaxed", muted)}>
          Feedback is about Overmind itself, never your code, prompts or data. Turn off anytime in
          Settings. <DocsLink />
        </p>
      </div>
    </div>
  );
}

/** Shown once to an account with no recorded choice: accounts made before the choice existed. */
export function AgentFeedbackNotice() {
  const { isGuest } = useAuthContext();
  const me = useOnboardingStatus(!isGuest);
  const choose = useAgentFeedbackChoice();
  if (isGuest || !me.data || me.data.agentFeedback !== null) return null;
  return (
    <section
      aria-label="Agent feedback"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/70 bg-background px-3 py-2 text-xs"
    >
      <Icon.agent aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      <p className="min-w-0 flex-1">
        Your coding agents can send feedback to Overmind when something fails, is unclear or is
        missing. <span className="text-muted-foreground">Never your code, prompts or data.</span>{" "}
        <DocsLink className="text-muted-foreground" />
      </p>
      <div className="flex shrink-0 gap-2">
        <Button
          disabled={choose.isPending}
          onClick={() => choose.mutate(false)}
          size="sm"
          variant="secondary"
        >
          Don’t allow
        </Button>
        <Button disabled={choose.isPending} onClick={() => choose.mutate(true)} size="sm">
          Allow
        </Button>
      </div>
    </section>
  );
}

export function AgentFeedbackSetting() {
  const me = useOnboardingStatus();
  const choose = useAgentFeedbackChoice();
  const id = useId();
  const decidedAt = me.data?.agentFeedbackAt;
  return (
    <div className="flex items-start justify-between gap-6 border border-border bg-card p-4">
      <div className="space-y-1">
        <label className="text-sm font-medium" htmlFor={id}>
          Agent feedback
        </label>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Your coding agents send feedback about Overmind itself, never your code, prompts or data.{" "}
          <DocsLink />
        </p>
        <p className="text-xs text-muted-foreground">
          {decidedAt ? (
            <>
              Chosen <DateTime value={decidedAt} />
            </>
          ) : (
            "Not chosen"
          )}
        </p>
      </div>
      <Switch
        checked={me.data?.agentFeedback === true}
        disabled={!me.data || choose.isPending}
        id={id}
        onCheckedChange={(checked) => choose.mutate(checked)}
      />
    </div>
  );
}
