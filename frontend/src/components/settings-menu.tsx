import { useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { cn } from "@/lib/utils";

const THEMES = [
  { icon: Icon.themeLight, label: "Light", value: "light" },
  { icon: Icon.themeDark, label: "Dark", value: "dark" },
  { icon: Icon.model, label: "System", value: "system" },
] as const;

export function SettingsMenuButton() {
  const navigate = useNavigate();
  const { theme, setTheme } = useTheme();
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

        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-3 py-2">
          <span className="text-sm text-muted-foreground">Theme</span>
          <div className="flex overflow-hidden rounded-md border border-border">
            {THEMES.map((t) => (
              <button
                aria-label={`${t.label} theme`}
                aria-pressed={theme === t.value}
                className={cn(
                  "flex h-7 items-center gap-1.5 px-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                  theme === t.value
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  t.value !== "light" && "border-l border-border"
                )}
                key={t.value}
                onClick={() => setTheme(t.value)}
                type="button"
              >
                <t.icon className="size-3.5" />
                {t.label}
              </button>
            ))}
          </div>
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
