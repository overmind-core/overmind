import { type ReactNode, type RefObject, useCallback, useEffect, useMemo, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { SearchFieldContext } from "@/components/ui/search-input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";

/** Toolbars run at 32px or 36px; 28px is for controls nested inside a popover. */
type ListToolbarSize = "default" | "lg";

function copyCurrentUrl() {
  if (typeof navigator === "undefined" || !navigator.clipboard) return;
  void navigator.clipboard.writeText(window.location.href).then(
    () => notify.success("Link copied"),
    () => notify.error("Couldn't copy the link")
  );
}

/** `/` focuses search, `r` clears filters, `c` copies the page link. */
export function useListShortcuts({
  onClearFilters,
  searchRef,
  shareable = false,
}: {
  onClearFilters?: () => void;
  searchRef?: RefObject<HTMLInputElement | null>;
  shareable?: boolean;
}) {
  // Read through a ref so an inline `onClearFilters` doesn't resubscribe the listener.
  const latest = useRef({ onClearFilters, searchRef, shareable });
  latest.current = { onClearFilters, searchRef, shareable };

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key !== "/" && e.key !== "r" && e.key !== "c") return;

      // Never steal a keystroke from a field, or act on the list behind an open dialog.
      const target = e.target as HTMLElement | null;
      if (target?.isContentEditable) return;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (document.querySelector('[role="dialog"][data-state="open"]')) return;

      const current = latest.current;
      if (e.key === "/") {
        const el = current.searchRef?.current;
        if (!el) return;
        e.preventDefault();
        el.focus();
        el.select();
      } else if (e.key === "r") {
        if (!current.onClearFilters) return;
        e.preventDefault();
        current.onClearFilters();
      } else if (current.shareable) {
        e.preventDefault();
        copyCurrentUrl();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}

export interface ListToolbarProps {
  primary?: ReactNode;
  /** A `SearchInput`. It inherits `size` and the `/` binding from here. */
  search?: ReactNode;
  filters?: ReactNode;
  actions?: ReactNode;
  /** Supplying it renders "Clear filters" while `hasActiveFilters`, and binds `r`. */
  onClearFilters?: () => void;
  hasActiveFilters?: boolean;
  /** The page keeps its filter state in the URL: renders "Copy link" and binds `c`. */
  shareable?: boolean;
  size?: ListToolbarSize;
  className?: string;
  /** Extra rows below the control row (quick filters, active filter chips). */
  children?: ReactNode;
}

export function ListToolbar({
  actions,
  children,
  className,
  filters,
  hasActiveFilters = false,
  onClearFilters,
  primary,
  search,
  shareable = false,
  size = "default",
}: ListToolbarProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  const register = useCallback((el: HTMLInputElement | null) => {
    searchRef.current = el;
  }, []);
  const searchContext = useMemo(() => ({ hint: "/", register, size }), [register, size]);

  useListShortcuts({ onClearFilters, searchRef, shareable });

  return (
    <TooltipProvider>
      <div className={cn("flex w-full min-w-0 flex-col gap-3", className)}>
        <div className="flex flex-wrap items-center gap-2">
          {primary}

          {search ? (
            <SearchFieldContext.Provider value={searchContext}>
              {search}
            </SearchFieldContext.Provider>
          ) : null}

          {filters}

          {onClearFilters && hasActiveFilters ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button onClick={onClearFilters} size={size} variant="secondary">
                  <Icon.undo />
                  Clear filters
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={6}>
                Clear filters (r)
              </TooltipContent>
            </Tooltip>
          ) : null}

          {shareable ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label="Copy link"
                  onClick={copyCurrentUrl}
                  size={size === "lg" ? "icon-lg" : "icon"}
                  variant="secondary"
                >
                  <Icon.copy />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={6}>
                Copy link (c)
              </TooltipContent>
            </Tooltip>
          ) : null}

          {actions}
        </div>

        {children}
      </div>
    </TooltipProvider>
  );
}
