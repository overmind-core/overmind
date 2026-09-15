import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SelectableCard, SelectableCardGroup } from "@/components/ui/selectable-card";
import { cn } from "@/lib/utils";

export type Option = { value: string; label: string; tag?: string };

export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Select…",
  searchPlaceholder = "Search…",
  ariaLabel,
  triggerClassName,
}: {
  options: Option[];
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  ariaLabel: string;
  triggerClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selected = options.find((o) => o.value === value) ?? null;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, search]);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) setSearch("");
  };
  const handleSelect = (v: string) => {
    onChange(v);
    setOpen(false);
    setSearch("");
  };

  return (
    <Popover onOpenChange={handleOpenChange} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label={ariaLabel}
          className={cn("justify-between font-normal", triggerClassName)}
          type="button"
          variant="secondary"
        >
          <span className="flex min-w-0 items-center gap-1.5 truncate">
            <span className="truncate">{selected?.label ?? placeholder}</span>
            {selected?.tag ? (
              <Badge className="shrink-0 tracking-normal" size="chip" variant="secondary">
                {selected.tag}
              </Badge>
            ) : null}
          </span>
          <Icon.chevronDown className="shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[--radix-popover-trigger-width] min-w-56 overflow-hidden p-0"
      >
        <div className="border-b p-1.5">
          <div className="relative">
            <Icon.search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label={searchPlaceholder}
              className="pl-8 text-xs"
              onChange={(e) => setSearch(e.target.value)}
              placeholder={searchPlaceholder}
              size="sm"
              value={search}
            />
          </div>
        </div>
        <SelectableCardGroup className="max-h-[280px] overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">No matches for "{search}".</p>
          ) : (
            filtered.map((o) => {
              const isSelected = o.value === value;
              return (
                <SelectableCard
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs",
                    !isSelected && "border-transparent bg-transparent hover:bg-muted"
                  )}
                  key={o.value}
                  onSelect={() => handleSelect(o.value)}
                  role="radio"
                  selected={isSelected}
                >
                  <Icon.success
                    className={cn("size-4 shrink-0", isSelected ? "opacity-100" : "opacity-0")}
                  />
                  <span className="truncate font-medium">{o.label}</span>
                  {o.tag ? (
                    <Badge
                      className="ml-auto shrink-0 tracking-normal"
                      size="chip"
                      variant="secondary"
                    >
                      {o.tag}
                    </Badge>
                  ) : null}
                </SelectableCard>
              );
            })
          )}
        </SelectableCardGroup>
      </PopoverContent>
    </Popover>
  );
}
