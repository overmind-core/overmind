import type { ComponentType, ReactNode, SVGProps } from "react";

import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

function EmptyState({
  icon: Icon,
  iconClassName,
  title,
  titleClassName,
  description,
  action,
  size = "page",
  className,
}: {
  icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** For two-tone glyphs (`invert [image-rendering:pixelated]`) that must not take
   *  the default muted-foreground fill. */
  iconClassName?: string;
  title: ReactNode;
  titleClassName?: string;
  description?: ReactNode;
  action?: ReactNode;
  size?: "page" | "section" | "compact";
  className?: string;
}) {
  const isPage = size === "page";
  const isCompact = size === "compact";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-4 text-center",
        isPage ? "min-h-[400px] flex-1" : isCompact ? "py-6" : "py-12",
        className
      )}
      data-slot="empty-state"
    >
      {Icon ? (
        <Icon
          aria-hidden
          className={cn(
            "text-muted-foreground",
            isCompact ? "mb-2 size-4" : isPage ? "mb-4 size-12" : "mb-4 size-8",
            iconClassName
          )}
        />
      ) : null}
      <p className={cn("mb-1", isCompact ? "text-sm font-medium" : TITLE.hero, titleClassName)}>
        {title}
      </p>
      {description ? (
        <p
          className={cn(
            PROSE,
            "mx-auto max-w-md text-muted-foreground",
            isCompact ? "text-xs" : "text-sm"
          )}
        >
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-6 flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

export { EmptyState };
