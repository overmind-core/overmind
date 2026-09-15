import { useEffect, useSyncExternalStore } from "react";

import { useNavigate } from "@tanstack/react-router";

import { trackEvent } from "@/analytics";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useAuthContext } from "@/contexts/auth-context";
import { dismissGuestPrompt, isGuestPromptOpen, onGuestUpgrade } from "@/lib/guest";

/**
 * The single surface for a guest gate: mounted once in the `_auth` layout,
 * opened by any gated click or `guest_upgrade_required` response.
 */
export function CreateAccountDialog() {
  const { isGuest } = useAuthContext();
  const navigate = useNavigate();
  const open = useSyncExternalStore(onGuestUpgrade, isGuestPromptOpen);

  useEffect(() => {
    if (open) trackEvent("guest_upgrade_prompted");
  }, [open]);

  useEffect(() => {
    if (!isGuest) dismissGuestPrompt();
  }, [isGuest]);

  if (!isGuest) return null;

  return (
    <ConfirmDialog
      cancelLabel="Not now"
      confirmLabel="Create account"
      description="This action needs an account. The scanned workspace moves to the new account."
      onConfirm={() => {
        dismissGuestPrompt();
        void navigate({ search: { mode: "signup" }, to: "/login" });
      }}
      onOpenChange={(next) => {
        if (!next) dismissGuestPrompt();
      }}
      open={open}
      title="Create an account to continue"
    />
  );
}
