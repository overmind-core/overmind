import { Icon } from "@/components/ui/icons";
import { cn, usdToOvermindCredits, usdToOvermindCreditsWithLabel } from "@/lib/utils";

/** The glyph replaces the word "credits" visually; screen readers still get the full
 *  phrase. */
export function CreditsAmount({ usd, className }: { usd: number; className?: string }) {
  return (
    <span
      aria-label={usdToOvermindCreditsWithLabel(usd)}
      className={cn("inline-flex items-center gap-1 align-middle tabular-nums", className)}
      title={usdToOvermindCreditsWithLabel(usd)}
    >
      <Icon.credits aria-hidden className="size-3.5 shrink-0" />
      {usdToOvermindCredits(usd).toLocaleString("en-US")}
    </span>
  );
}
