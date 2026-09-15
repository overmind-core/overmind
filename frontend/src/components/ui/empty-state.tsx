import type { ComponentType, ReactNode, SVGProps } from "react";

import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

function EmptyState({
  icon: Icon,
  iconClassName,
  title,
  description,
  action,
  size = "page",
  className,
}: {
  icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** For two-tone glyphs (`dark:invert [image-rendering:pixelated]`) that must not take
   *  the default muted-foreground fill. */
  iconClassName?: string;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  size?: "page" | "section";
  className?: string;
}) {
  const isPage = size === "page";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-4 text-center",
        isPage ? "min-h-[400px] flex-1" : "py-12",
        className
      )}
      data-slot="empty-state"
    >
      {Icon ? (
        <Icon
          className={cn("mb-4 text-muted-foreground", isPage ? "size-12" : "size-8", iconClassName)}
        />
      ) : null}
      {/* Always the hero step: the title ramp reserves it for empty-state headlines, and
          `section` only shrinks the icon and padding. */}
      <p className={cn("mb-1", TITLE.hero)}>{title}</p>
      {description ? (
        <p className={cn(PROSE, "mx-auto max-w-md text-sm text-muted-foreground")}>{description}</p>
      ) : null}
      {action ? <div className="mt-6 flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

export { EmptyState };
