"use client";

import type * as React from "react";

import { Accordion as AccordionPrimitive } from "radix-ui";

import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

function Accordion({ ...props }: React.ComponentProps<typeof AccordionPrimitive.Root>) {
  return <AccordionPrimitive.Root data-slot="accordion" {...props} />;
}

function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item
      className={cn("border-b last:border-b-0", className)}
      data-slot="accordion-item"
      {...props}
    />
  );
}

function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>) {
  return (
    <AccordionPrimitive.Header className="flex">
      <AccordionPrimitive.Trigger
        className={cn(
          "focus-visible:border-ring focus-visible:ring-ring/50 flex flex-1 items-start justify-between gap-4 rounded-md py-4 text-left text-sm font-medium transition-all outline-none focus-visible:ring-[3px] disabled:pointer-events-none disabled:opacity-50 [&[data-state=open]>svg]:scale-y-[-1]",
          className
        )}
        data-slot="accordion-trigger"
        {...props}
      >
        {children}
        <Icon.chevronDown className="text-muted-foreground pointer-events-none size-4 shrink-0 translate-y-0.5 transition-transform duration-200" />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      className="group data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down overflow-y-clip overflow-x-visible text-sm"
      data-slot="accordion-content"
      {...props}
    >
      <div
        className={cn(
          "pt-0 pb-4",
          "group-data-[state=open]:animate-in group-data-[state=open]:fade-in-0",
          "group-data-[state=closed]:animate-out group-data-[state=closed]:fade-out-0",
          "duration-200",
          className
        )}
      >
        {children}
      </div>
    </AccordionPrimitive.Content>
  );
}

export { Accordion, AccordionContent, AccordionItem, AccordionTrigger };
