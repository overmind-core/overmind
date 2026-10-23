import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";

export function Stat({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "warning" | "success";
}) {
  return (
    <Card className="space-y-1 bg-wash-subtle px-4 py-3">
      <p className={cn(LABEL.pixel, "text-xs text-muted-foreground")}>{label}</p>
      <p
        className={cn(
          "font-mono text-2xl tabular-nums text-foreground",
          tone === "warning" && "text-warning",
          tone === "success" && "text-success"
        )}
      >
        {value}
      </p>
      {detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}
    </Card>
  );
}
