import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// `header` is required and takes a `<PageHeader>`. A page with genuinely no title
// passes `header={null}` — a decision, not a slot someone forgot.
function PageShell({
  variant = "scroll",
  header,
  children,
  className,
}: {
  variant?: "scroll" | "full";
  header: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  if (variant === "full") {
    return (
      <div
        className={cn("flex h-full min-h-0 flex-1 flex-col gap-4", className)}
        data-slot="page-shell"
      >
        {/* The shell owns the header's shrink behaviour, not the call site. */}
        {header ? <div className="shrink-0">{header}</div> : null}
        {children}
      </div>
    );
  }
  return (
    <div className={cn("page-wrapper", className)} data-slot="page-shell">
      {header}
      {children}
    </div>
  );
}

export { PageShell };
