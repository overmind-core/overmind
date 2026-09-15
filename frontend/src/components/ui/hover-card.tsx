/** `openDelay` below ~250ms flickers cards at a pointer crossing a line of triggers.
 *  `closeDelay` is the grace period for crossing the trigger-to-card gap, so
 *  `sideOffset` stays small — a wide gap is a wide dead zone. */

import type * as React from "react";

import { HoverCard as HoverCardPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function HoverCard({
  openDelay = 350,
  closeDelay = 250,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Root>) {
  return (
    <HoverCardPrimitive.Root
      closeDelay={closeDelay}
      data-slot="hover-card"
      openDelay={openDelay}
      {...props}
    />
  );
}

function HoverCardTrigger({ ...props }: React.ComponentProps<typeof HoverCardPrimitive.Trigger>) {
  return <HoverCardPrimitive.Trigger data-slot="hover-card-trigger" {...props} />;
}

function HoverCardContent({
  className,
  align = "start",
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal>
      <HoverCardPrimitive.Content
        align={align}
        className={cn(
          "bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-1 data-[side=left]:slide-in-from-right-1 data-[side=right]:slide-in-from-left-1 data-[side=top]:slide-in-from-bottom-1 z-50 w-80 origin-(--radix-hover-card-content-transform-origin) rounded-md border border-border outline-none",
          className
        )}
        // Keeps a card summoned from a table's last row or rightmost column inside the
        // viewport instead of half off-screen.
        collisionPadding={12}
        data-slot="hover-card-content"
        sideOffset={sideOffset}
        {...props}
      />
    </HoverCardPrimitive.Portal>
  );
}

export { HoverCard, HoverCardContent, HoverCardTrigger };
