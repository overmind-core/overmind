import type * as React from "react";
import { createContext, useContext } from "react";

import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** The shared control ramp: xs 24px · sm 28px · default 32px · lg 36px. */
type ControlSize = "xs" | "sm" | "default" | "lg";

interface SearchFieldContextValue {
  size: ControlSize;
  /** Hands the input element to the toolbar's `/` shortcut. */
  register: (el: HTMLInputElement | null) => void;
  /** Key advertised in the trailing chip while the field is empty. */
  hint?: string;
}

/** Supplied by `ListToolbar`; absent elsewhere, so `SearchInput` works standalone. */
export const SearchFieldContext = createContext<SearchFieldContextValue | null>(null);

export interface SearchInputProps
  extends Omit<React.ComponentProps<"input">, "size" | "type" | "value"> {
  /** Accessible name. The glyph is decorative, so this is the field's only name. */
  label: string;
  value: string;
  onClear?: () => void;
  /** Defaults to the enclosing toolbar's size, then to 32px. */
  size?: ControlSize;
  /** Layout only (width, flex). Glyph offset and padding belong to the primitive. */
  className?: string;
  ref?: React.Ref<HTMLInputElement>;
}

export function SearchInput({
  className,
  label,
  onClear,
  ref,
  size,
  value,
  ...props
}: SearchInputProps) {
  const toolbar = useContext(SearchFieldContext);
  const resolvedSize = size ?? toolbar?.size ?? "default";
  const showClear = !!onClear && value !== "";
  const hint = value === "" ? toolbar?.hint : undefined;

  return (
    <div className={cn("relative", className)}>
      {/* left-3 + the input's 1px border puts the glyph on the same 13px inset as the
          input's own text, so a search field and the select beside it align. */}
      <Icon.search
        aria-hidden
        className={cn(
          "pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted-foreground",
          resolvedSize === "xs" ? "left-2 size-3" : "left-3 size-3.5"
        )}
      />
      <Input
        aria-label={label}
        autoComplete="off"
        {...props}
        className={cn(
          // The xs ramp sets `px-2` through a data-variant, which plain `pl-*` cannot outrank.
          resolvedSize === "xs" ? "data-[size=xs]:pl-7" : "pl-9",
          // WebKit paints its own cancel glyph on a search input.
          "[&::-webkit-search-cancel-button]:appearance-none [&::-webkit-search-decoration]:appearance-none",
          (resolvedSize === "sm" || resolvedSize === "xs") && "text-xs",
          (showClear || hint) && (resolvedSize === "xs" ? "data-[size=xs]:pr-7" : "pr-8")
        )}
        ref={(el) => {
          toolbar?.register(el);
          if (typeof ref === "function") ref(el);
          else if (ref) ref.current = el;
        }}
        size={resolvedSize}
        type="search"
        value={value}
      />
      {showClear && (
        <button
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm text-muted-foreground transition-colors duration-150 hover:text-foreground"
          onClick={onClear}
          type="button"
        >
          <Icon.close className="size-3.5" />
        </button>
      )}
      {!showClear && hint && (
        <kbd
          aria-hidden
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-xs border border-border/60 px-1 text-xs leading-none text-muted-foreground"
        >
          {hint}
        </kbd>
      )}
    </div>
  );
}
