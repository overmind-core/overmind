"use client";

import * as React from "react";

import { cva, type VariantProps } from "class-variance-authority";
import { Dialog as SheetPrimitive } from "radix-ui";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

function Sheet({ ...props }: React.ComponentProps<typeof SheetPrimitive.Root>) {
  return <SheetPrimitive.Root data-slot="sheet" {...props} />;
}

function SheetClose({ ...props }: React.ComponentProps<typeof SheetPrimitive.Close>) {
  return <SheetPrimitive.Close data-slot="sheet-close" {...props} />;
}

function SheetPortal({ ...props }: React.ComponentProps<typeof SheetPrimitive.Portal>) {
  return <SheetPrimitive.Portal data-slot="sheet-portal" {...props} />;
}

function SheetOverlay({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Overlay>) {
  return (
    <SheetPrimitive.Overlay
      className={cn(
        "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 fixed inset-0 z-50 bg-scrim",
        className
      )}
      data-slot="sheet-overlay"
      {...props}
    />
  );
}

// A sheet keeps the full extent along its anchored edge and takes its size on the other
// axis, stopping at the same 75% canvas ceiling every modal obeys.
const sheetContentVariants = cva(
  "bg-background data-[state=open]:animate-in data-[state=closed]:animate-out fixed z-50 flex flex-col overflow-hidden transition ease-in-out data-[state=closed]:duration-300 data-[state=open]:duration-500",
  {
    compoundVariants: [
      { class: "w-[24rem]", side: ["left", "right"], size: "sm" },
      { class: "w-[32rem]", side: ["left", "right"], size: "md" },
      { class: "w-[48rem]", side: ["left", "right"], size: "lg" },
      { class: "w-[75vw]", side: ["left", "right"], size: "full" },
      { class: "h-[24rem]", side: ["top", "bottom"], size: "sm" },
      { class: "h-[32rem]", side: ["top", "bottom"], size: "md" },
      { class: "h-[48rem]", side: ["top", "bottom"], size: "lg" },
      { class: "h-[75vh]", side: ["top", "bottom"], size: "full" },
    ],
    defaultVariants: { side: "right", size: "md" },
    variants: {
      side: {
        bottom:
          "data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom inset-x-0 bottom-0 max-h-[75vh] w-full border-t",
        left: "data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left inset-y-0 left-0 h-full max-w-[75vw] border-r",
        right:
          "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right inset-y-0 right-0 h-full max-w-[75vw] border-l",
        top: "data-[state=closed]:slide-out-to-top data-[state=open]:slide-in-from-top inset-x-0 top-0 max-h-[75vh] w-full border-b",
      },
      size: { full: "", lg: "", md: "", sm: "" },
    },
  }
);

function SheetContent({
  className,
  children,
  closeButtonClassName,
  side = "right",
  size,
  showCloseButton = true,
  floatingClose = false,
  showOverlay = true,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content> &
  VariantProps<typeof sheetContentVariants> & {
    /** Position of a `floatingClose` button, for content whose own header puts its
     *  button row at a different offset than `SheetHeader`. */
    closeButtonClassName?: string;
    showCloseButton?: boolean;
    /** For a sheet with no `SheetHeader` to hold the close button. */
    floatingClose?: boolean;
    showOverlay?: boolean;
  }) {
  return (
    <SheetPortal>
      {showOverlay && <SheetOverlay />}
      <SheetPrimitive.Content
        className={cn(sheetContentVariants({ className, side, size }))}
        data-slot="sheet-content"
        {...props}
      >
        <SheetCloseSlotContext.Provider value={showCloseButton && !floatingClose}>
          {children}
        </SheetCloseSlotContext.Provider>
        {showCloseButton && floatingClose && (
          <SheetClose asChild>
            <Button
              aria-label="Close"
              className={cn("absolute top-3 right-3", closeButtonClassName)}
              size="icon-sm"
              variant="secondary"
            >
              <Icon.close />
            </Button>
          </SheetClose>
        )}
      </SheetPrimitive.Content>
    </SheetPortal>
  );
}

/** Read by `SheetHeader`, which owns the close button so it shares one centred
 *  row with the title. See the same context in `ui/dialog.tsx`. */
const SheetCloseSlotContext = React.createContext(true);

// Same three slots, same `px-5`, as Dialog — a sheet is a modal on its side.
function SheetHeader({
  className,
  children,
  end,
  ...props
}: React.ComponentProps<"div"> & {
  /** Trailing controls, centred against the title. */
  end?: React.ReactNode;
}) {
  const showCloseButton = React.useContext(SheetCloseSlotContext);
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-between gap-4 border-b border-border/70 px-5 py-4 text-left",
        className
      )}
      data-slot="sheet-header"
      {...props}
    >
      <div className="flex min-w-0 flex-col gap-0.5">{children}</div>
      {(end || showCloseButton) && (
        <div className="flex shrink-0 items-center gap-3">
          {end}
          {showCloseButton && (
            <SheetClose asChild>
              <Button aria-label="Close" size="icon-sm" variant="secondary">
                <Icon.close />
              </Button>
            </SheetClose>
          )}
        </div>
      )}
    </div>
  );
}

function SheetBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("min-h-0 flex-1 overflow-y-auto px-5 py-4", className)}
      data-slot="sheet-body"
      {...props}
    />
  );
}

function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-row items-center justify-end gap-2 border-t border-border/70 px-5 py-3",
        className
      )}
      data-slot="sheet-footer"
      {...props}
    />
  );
}

function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return (
    <SheetPrimitive.Title
      className={cn(TITLE.card, "text-foreground", className)}
      data-slot="sheet-title"
      {...props}
    />
  );
}

function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return (
    <SheetPrimitive.Description
      className={cn(PROSE, "text-muted-foreground text-sm", className)}
      data-slot="sheet-description"
      {...props}
    />
  );
}

export { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle };
