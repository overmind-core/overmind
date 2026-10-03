import type { ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Spinner } from "@/components/ui/spinner";

function ConfirmDialog({
  trigger,
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  error = null,
  isPending = false,
  keepOpenOnError = false,
  onConfirm,
}: {
  trigger?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  /** Restated inside the dialog, since a toast can fade before it is read. */
  error?: Error | null;
  isPending?: boolean;
  /** Await `onConfirm` and keep the dialog open if it rejects. Requires an awaitable
   *  `onConfirm`. */
  keepOpenOnError?: boolean;
  onConfirm: () => void | Promise<void>;
}) {
  return (
    <AlertDialog onOpenChange={onOpenChange} open={open}>
      {trigger ? <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger> : null}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description ? <AlertDialogDescription>{description}</AlertDialogDescription> : null}
        </AlertDialogHeader>
        {error && (
          <AlertDialogBody>
            <DismissibleAlert error={error} variant="warning" />
          </AlertDialogBody>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            disabled={isPending}
            onClick={(e) => {
              // Block the default auto-close so the `isPending` spinner stays visible.
              if (isPending) e.preventDefault();
              if (keepOpenOnError) {
                // Close only once `onConfirm` resolves, so a rejection stays visible.
                e.preventDefault();
                void Promise.resolve(onConfirm())
                  .then(() => onOpenChange?.(false))
                  .catch(() => {
                    /* stay open; the caller's onError surfaces the reason */
                  });
                return;
              }
              void onConfirm();
            }}
            variant={destructive ? "destructive" : "default"}
          >
            {isPending ? <Spinner className="text-current" size="sm" /> : null}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export { ConfirmDialog };
