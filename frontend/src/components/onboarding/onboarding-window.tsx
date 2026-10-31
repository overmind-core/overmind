import type { ReactNode } from "react";

import overmindEye from "@/assets/onboarding/overmind.svg";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function OnboardingWindow({
  children,
  title,
  className,
}: {
  children: ReactNode;
  title: string;
  className?: string;
}) {
  return (
    <Card
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-none border-background",
        className
      )}
    >
      <div className="grid h-7 shrink-0 grid-cols-[24px_1fr_auto_1fr_24px] items-center gap-2 border-b border-background bg-foreground px-1 text-background">
        <span aria-hidden="true" />
        <span
          aria-hidden="true"
          className="h-[17px] bg-[repeating-linear-gradient(to_bottom,currentColor_0px,currentColor_2px,transparent_2px,transparent_5px)]"
        />
        <span className="px-1 text-sm font-medium leading-none">{title}</span>
        <span
          aria-hidden="true"
          className="h-[17px] bg-[repeating-linear-gradient(to_bottom,currentColor_0px,currentColor_2px,transparent_2px,transparent_5px)]"
        />
        <img
          alt="Overmind"
          className="justify-self-center [image-rendering:pixelated]"
          src={overmindEye}
        />
      </div>
      <div className="min-h-0 overflow-y-auto bg-foreground p-1">
        <div className="space-y-3 bg-card p-4">{children}</div>
      </div>
    </Card>
  );
}
