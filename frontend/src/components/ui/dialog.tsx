import * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";
import { Dialog as DialogPrimitive } from "radix-ui";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

function Dialog({ ...props }: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogTrigger({ ...props }: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogPortal({ ...props }: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal {...props} />;
}

function DialogClose({ ...props }: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />;
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      className={cn(
        "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-scrim",
        className
      )}
      data-slot="dialog-overlay"
      {...props}
    />
  );
}

// The canvas is fixed: no modal is ever wider than 75vw or taller than 75vh. A size
// picks only the natural width inside that ceiling.
const dialogContentVariants = cva(
  "bg-background text-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 fixed top-[50%] left-[50%] z-50 flex max-h-[75vh] w-full max-w-[75vw] translate-x-[-50%] translate-y-[-50%] flex-col overflow-hidden rounded-md border border-border duration-200",
  {
    defaultVariants: { size: "md" },
    variants: {
      size: {
        full: "h-[75vh] w-[75vw]",
        lg: "sm:w-[56rem]",
        md: "sm:w-[40rem]",
        sm: "sm:w-[26rem]",
        // Multi-step: the height is fixed so the footer buttons hold still
        // between steps whose content differs.
        wizard: "h-[36rem] sm:w-[56rem]",
      },
    },
  }
);

function DialogContent({
  className,
  children,
  size,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> &
  VariantProps<typeof dialogContentVariants> & {
    showCloseButton?: boolean;
  }) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        className={cn(dialogContentVariants({ className, size }))}
        data-slot="dialog-content"
        {...props}
      >
        <DialogCloseSlotContext.Provider value={showCloseButton}>
          {children}
        </DialogCloseSlotContext.Provider>
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

/** Read by `DialogHeader`, which owns the close button so it shares one centred
 *  row with the title. An absolutely-placed button cannot centre on a header
 *  whose height changes with the description and the trailing slot. */
const DialogCloseSlotContext = React.createContext(true);

/** Header and footer hold their height; the body takes the rest and is the only thing
 *  that scrolls. All three share `px-5`, so body fields line up with the title. */
function DialogHeader({
  className,
  children,
  end,
  ...props
}: React.ComponentProps<"div"> & {
  /** Trailing controls — a stepper, a toggle, an action. Centred against the title. */
  end?: React.ReactNode;
}) {
  const showCloseButton = React.useContext(DialogCloseSlotContext);
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between gap-4 border-b border-border/70 px-5 py-4 text-left",
        className
      )}
      data-slot="dialog-header"
      {...props}
    >
      {/* Tight: the title and its description are one unit, and both faces
          already carry half-leading above and below. */}
      <div className="flex min-w-0 flex-col gap-0.5">{children}</div>
      {(end || showCloseButton) && (
        <div className="flex shrink-0 items-center gap-3">
          {end}
          {showCloseButton && (
            <DialogClose asChild>
              <Button aria-label="Close" size="icon-sm" variant="secondary">
                <Icon.close />
              </Button>
            </DialogClose>
          )}
        </div>
      )}
    </div>
  );
}

function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      // `min-h-0` lets this shrink inside the flex column; without it the body grows to
      // its content and the modal overflows the canvas.
      className={cn("min-h-0 flex-1 overflow-y-auto px-5 py-4", className)}
      data-slot="dialog-body"
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-row items-center justify-end gap-2 border-t border-border/70 px-5 py-3",
        className
      )}
      data-slot="dialog-footer"
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn(TITLE.card, "text-foreground", className)}
      data-slot="dialog-title"
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn(PROSE, "text-muted-foreground text-sm", className)}
      data-slot="dialog-description"
      {...props}
    />
  );
}

export {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
};
