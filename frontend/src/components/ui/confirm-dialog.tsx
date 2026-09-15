import type { ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

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
        <DismissibleAlert error={error} variant="warning" />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            className={cn(destructive && buttonVariants({ variant: "destructive" }), "gap-1.5")}
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
