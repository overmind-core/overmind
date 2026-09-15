import type * as React from "react";

import { cn } from "@/lib/utils";

// One control-height ramp shared with Button and SelectTrigger (xs 24px · sm 28px ·
// default 32px · lg 36px) and one content box (1px border + `px-3`). Align a row of controls by giving
// them the same `size`, never by patching `h-*` onto one (`check:design` forbids that).
function Input({
  className,
  type,
  size = "default",
  ...props
}: Omit<React.ComponentProps<"input">, "size"> & { size?: "xs" | "sm" | "default" | "lg" }) {
  return (
    <input
      className={cn(
        "file:text-foreground placeholder:text-muted-foreground/80 selection:bg-primary selection:text-primary-foreground border border-input w-full min-w-0 rounded-sm bg-transparent px-3 text-sm transition-colors duration-150 outline-none data-[size=xs]:h-6 data-[size=xs]:px-2 data-[size=xs]:text-xs data-[size=sm]:h-7 data-[size=default]:h-8 data-[size=lg]:h-9",
        "focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40",
        "aria-invalid:border-destructive",
        className
      )}
      data-size={size}
      data-slot="input"
      type={type}
      {...props}
    />
  );
}

export { Input };
