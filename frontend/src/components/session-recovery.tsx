import { useState, useSyncExternalStore } from "react";

import api from "@/client";
import { LocalLoginForm } from "@/components/local-login-form";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { config } from "@/config";
import { useAuthContext } from "@/contexts/auth-context";
import { getContext } from "@/integrations/tanstack-query";
import { errorMessage } from "@/lib/notify";
import {
  dismissSessionRecovery,
  sessionRecoveryRequired,
  subscribeSessionRecovery,
} from "@/lib/session-recovery";

export function SessionRecovery() {
  const required = useSyncExternalStore(subscribeSessionRecovery, sessionRecoveryRequired);
  const { signOut, isSignedIn, user, refreshAuth } = useAuthContext();
  const canRestoreInPlace = !config.clerkReady && Boolean(user?.email);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function recover() {
    setBusy(true);
    setError(null);
    try {
      await api.auth.authMeRetrieve();
      dismissSessionRecovery();
      void getContext().queryClient.invalidateQueries();
    } catch (err) {
      setError(errorMessage(err, "Could not restore your session."));
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    setBusy(true);
    setError(null);
    try {
      await signOut();
    } catch (err) {
      setError(errorMessage(err, "Could not sign out. Try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={required && isSignedIn}>
      <DialogContent
        className="z-80 motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none"
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        overlayClassName="z-70 motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none"
        showCloseButton={false}
        size="sm"
      >
        <DialogHeader>
          <DialogTitle>Session interrupted</DialogTitle>
          <DialogDescription>Your session could not be verified.</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3 text-sm">
          <p>The current page is still open. Try reconnecting, or sign in again to continue.</p>
          {canRestoreInPlace ? (
            <LocalLoginForm
              onSignedIn={() => {
                setError(null);
                dismissSessionRecovery();
                refreshAuth();
              }}
              recoveryEmail={user?.email}
            />
          ) : (
            <p className="text-muted-foreground">
              Signing in again leaves this page. Unsaved changes will be lost.
            </p>
          )}
          {error && (
            <p className="text-muted-foreground" role="alert">
              {error}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          {!canRestoreInPlace && (
            <Button disabled={busy} onClick={() => void restart()} variant="outline">
              Sign in again
            </Button>
          )}
          <Button
            disabled={busy}
            onClick={() => void recover()}
            variant={canRestoreInPlace ? "outline" : "default"}
          >
            {busy && <Spinner size="sm" />}Reconnect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
