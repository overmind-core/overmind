import type * as React from "react";

import {
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandRoot,
  CommandSeparator,
} from "cmdk";

import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

function Command({ className, ...props }: React.ComponentProps<typeof CommandRoot>) {
  return (
    <CommandRoot
      className={cn(
        "flex h-full w-full flex-col overflow-hidden rounded-md bg-popover text-popover-foreground",
        className
      )}
      data-slot="command"
      {...props}
    />
  );
}

function CommandPaletteInput({ className, ...props }: React.ComponentProps<typeof CommandInput>) {
  return (
    <div
      className="flex items-center border-b border-border/70 px-3"
      data-slot="command-input-wrapper"
    >
      <Icon.search aria-hidden="true" className="mr-2 size-4 shrink-0 text-muted-foreground" />
      <CommandInput
        className={cn(
          "flex h-11 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        data-slot="command-input"
        {...props}
      />
    </div>
  );
}

function CommandPaletteList({ className, ...props }: React.ComponentProps<typeof CommandList>) {
  return (
    <CommandList
      className={cn("max-h-[300px] scroll-py-1 overflow-x-hidden overflow-y-auto", className)}
      data-slot="command-list"
      {...props}
    />
  );
}

function CommandPaletteEmpty({ className, ...props }: React.ComponentProps<typeof CommandEmpty>) {
  return (
    <CommandEmpty
      className={cn("py-6 text-center text-sm text-muted-foreground", className)}
      data-slot="command-empty"
      {...props}
    />
  );
}

function CommandPaletteGroup({ className, ...props }: React.ComponentProps<typeof CommandGroup>) {
  return (
    <CommandGroup
      className={cn(
        "overflow-hidden p-1 text-foreground [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground",
        className
      )}
      data-slot="command-group"
      {...props}
    />
  );
}

function CommandPaletteItem({ className, ...props }: React.ComponentProps<typeof CommandItem>) {
  return (
    <CommandItem
      className={cn(
        "relative flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none",
        "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
        "data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50",
        "[&_svg]:pointer-events-none [&_svg]:shrink-0",
        className
      )}
      data-slot="command-item"
      {...props}
    />
  );
}

function CommandPaletteSeparator({
  className,
  ...props
}: React.ComponentProps<typeof CommandSeparator>) {
  return (
    <CommandSeparator
      className={cn("-mx-1 h-px bg-border", className)}
      data-slot="command-separator"
      {...props}
    />
  );
}

export {
  Command,
  CommandPaletteEmpty as CommandEmpty,
  CommandPaletteGroup as CommandGroup,
  CommandPaletteInput as CommandInput,
  CommandPaletteItem as CommandItem,
  CommandPaletteList as CommandList,
  CommandPaletteSeparator as CommandSeparator,
};
