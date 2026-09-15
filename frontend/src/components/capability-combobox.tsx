import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { formatSmart } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { CapabilityList } from "@/openapi";

export type CapabilityComboboxOption = {
  value: string;
  label: string;
  hint?: string | null;
};

const capabilityHint = (capability: CapabilityList): string | null => {
  const parts: string[] = [];
  if (capability.traceCount != null && capability.traceCount > 0) {
    parts.push(`${capability.traceCount} trace${capability.traceCount === 1 ? "" : "s"}`);
  }
  if (capability.lastActivityAt) parts.push(`active ${formatSmart(capability.lastActivityAt)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
};

const useDebouncedValue = (value: string, delayMs: number): string => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
};

export function CapabilityCombobox({
  value,
  onChange,
  projectId,
  options,
  excludeIds,
  placeholder = "Select a capability",
  searchPlaceholder = "Search capabilities…",
  ariaLabel = "Select capability",
  valueLabel,
  id,
  className,
  disabled,
}: {
  value: string;
  onChange: (value: string, option?: CapabilityComboboxOption) => void;
  /** Server-search mode: options are fetched and `?search=` does the filtering. */
  projectId?: string;
  /** Static mode: caller-supplied options, filtered client-side by cmdk. */
  options?: CapabilityComboboxOption[];
  /** Server-search mode only. */
  excludeIds?: string[];
  placeholder?: string;
  searchPlaceholder?: string;
  ariaLabel?: string;
  /** Fallback label for the current value when it isn't in the fetched page. */
  valueLabel?: string;
  id?: string;
  className?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);

  const serverMode = !options;
  const query = useDebouncedValue(search, 250).trim();

  const capabilitiesQuery = useQuery({
    enabled: serverMode && !!projectId && open,
    queryFn: () =>
      apiClient.capabilities.capabilitiesList({
        ordering: "name",
        pageSize: 100,
        project: projectId!,
        search: query || undefined,
      }),
    queryKey: ["capability-combobox", projectId, query],
  });

  const fetched: CapabilityComboboxOption[] = (capabilitiesQuery.data?.results ?? [])
    .filter((a) => !excludeIds?.includes(a.id))
    .map((a) => ({
      hint: capabilityHint(a),
      label: a.name || a.slug,
      value: a.id,
    }));

  const items = options ?? fetched;
  const displayLabel = value
    ? (items.find((o) => o.value === value)?.label ?? pickedLabel ?? valueLabel ?? null)
    : null;

  const handleSelect = (option: CapabilityComboboxOption) => {
    onChange(option.value, option);
    setPickedLabel(option.label);
    setOpen(false);
    setSearch("");
  };

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <button
          aria-expanded={open}
          aria-label={ariaLabel}
          className={cn(
            "flex h-8 w-fit items-center justify-between gap-2 rounded-sm border border-transparent bg-control px-3 text-sm whitespace-nowrap transition-[color,box-shadow] outline-none",
            "hover:bg-control-hover focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
          disabled={disabled}
          id={id}
          role="combobox"
          type="button"
        >
          <span className={cn("truncate", !displayLabel && "text-muted-foreground")}>
            {displayLabel ?? placeholder}
          </span>
          <Icon.chevronDown className="size-4 shrink-0 text-muted-foreground opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-64 p-0">
        <Command shouldFilter={!serverMode}>
          <CommandInput
            className="h-9"
            onValueChange={setSearch}
            placeholder={searchPlaceholder}
            value={search}
          />
          <CommandList>
            <CommandEmpty>
              {serverMode && capabilitiesQuery.isLoading ? "Loading…" : "No capabilities found."}
            </CommandEmpty>
            <CommandGroup>
              {items.map((option) => (
                <CommandItem
                  key={option.value}
                  onSelect={() => handleSelect(option)}
                  value={serverMode ? option.value : `${option.label} ${option.value}`}
                >
                  <Icon.success
                    className={cn("size-3.5", value === option.value ? "opacity-100" : "opacity-0")}
                  />
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {option.hint ? (
                    <span className="shrink-0 text-xs text-muted-foreground">{option.hint}</span>
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
