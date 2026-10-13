import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useGuestGate } from "@/hooks/use-guest-gate";

export function SettingsMenuButton() {
  const navigate = useNavigate();
  const guard = useGuestGate();
  const [open, setOpen] = useState(false);

  const go = (fn: () => void) => {
    setOpen(false);
    guard(fn)();
  };

  const jumpRows = [
    {
      icon: Icon.integrations,
      label: "Integrations",
      onSelect: () => void navigate({ search: (prev) => prev, to: "/observability/integrations" }),
    },
    {
      icon: Icon.credits,
      label: "Plan & billing",
      onSelect: () => void navigate({ search: (prev) => prev, to: "/settings" }),
    },
  ];

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button aria-label="Settings" size="icon" variant="secondary">
                <Icon.settings />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>
            Settings
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent align="end" className="w-72 overflow-hidden p-0" sideOffset={6}>
        <div className="flex items-center justify-between border-b border-border/70 py-1.5 pl-3 pr-1.5">
          <p className="text-sm font-medium text-foreground">Settings</p>
          <Button
            className="gap-0.5 text-muted-foreground hover:text-foreground"
            onClick={() => go(() => void navigate({ search: (prev) => prev, to: "/settings" }))}
            size="xs"
            variant="secondary"
          >
            Open settings
            <Icon.chevronRight className="size-3" />
          </Button>
        </div>

        <div className="p-1">
          {jumpRows.map((row) => (
            <button
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
              key={row.label}
              onClick={() => go(row.onSelect)}
              type="button"
            >
              <row.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{row.label}</span>
              <Icon.chevronRight className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
