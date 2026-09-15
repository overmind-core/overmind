import { type ReactElement, useState } from "react";

import { Button } from "@/components/ui/button";
import { CreditsAmount } from "@/components/ui/credits";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { useTopUpCredits } from "@/hooks/use-subscription";
import { notify } from "@/lib/notify";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

// Mirrors MIN_TOPUP_USD / MAX_TOPUP_USD on the server, which rejects anything else.
const MIN_USD = 1;
const MAX_USD = 1000;
const DEFAULT_USD = 25;
const PRESETS_USD = [10, 25, 50, 100];

function clamp(usd: number): number {
  return Math.min(MAX_USD, Math.max(MIN_USD, usd));
}

/** Whole dollars only: the Stripe line item is one credit at $0.01, quantity = credit count. */
export function TopUpDialog({
  trigger,
  open: openProp,
  onOpenChange,
}: {
  trigger?: ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [selfOpen, setSelfOpen] = useState(false);
  // The text field is the source of truth so it can hold a half-typed value
  // ("" or "3" on the way to "30") without the slider yanking it back.
  const [draft, setDraft] = useState(String(DEFAULT_USD));
  const topUp = useTopUpCredits();

  const open = openProp ?? selfOpen;
  const setOpen = (next: boolean) => {
    if (openProp === undefined) setSelfOpen(next);
    onOpenChange?.(next);
  };

  const amountUsd = Number.parseInt(draft, 10);
  const isValid = Number.isInteger(amountUsd) && amountUsd >= MIN_USD && amountUsd <= MAX_USD;

  async function startCheckout() {
    if (!isValid) return;
    try {
      const res = await topUp.mutateAsync(amountUsd);
      window.location.href = res.checkoutUrl;
    } catch (err) {
      notify.error(err, "Could not start checkout");
    }
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      {openProp === undefined && (
        <DialogTrigger asChild>
          {trigger ?? (
            <Button size="sm" variant="secondary">
              <Icon.add />
              Add credits
            </Button>
          )}
        </DialogTrigger>
      )}

      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add credits</DialogTitle>
          <DialogDescription>
            Credits pay for training runs, inference, and workshop analysis. They never expire.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          <div className="flex items-baseline justify-between gap-3">
            <span className={cn(TITLE.card, "text-foreground tabular-nums")}>
              ${isValid ? amountUsd.toLocaleString("en-US") : "—"}
            </span>
            {isValid && <CreditsAmount className="text-lg text-muted-foreground" usd={amountUsd} />}
          </div>

          <Slider
            aria-label="Top-up amount in US dollars"
            max={MAX_USD}
            min={MIN_USD}
            onValueChange={([next]) => setDraft(String(next))}
            step={1}
            value={[isValid ? amountUsd : MIN_USD]}
          />

          <div className="flex flex-wrap gap-2">
            {PRESETS_USD.map((preset) => (
              <Button
                key={preset}
                onClick={() => setDraft(String(preset))}
                size="sm"
                variant={amountUsd === preset ? "default" : "secondary"}
              >
                ${preset}
              </Button>
            ))}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="topup-amount">Exact amount (USD)</Label>
            <Input
              aria-invalid={draft !== "" && !isValid}
              className="w-32"
              id="topup-amount"
              inputMode="numeric"
              max={MAX_USD}
              min={MIN_USD}
              onBlur={() =>
                setDraft(String(Number.isFinite(amountUsd) ? clamp(amountUsd) : DEFAULT_USD))
              }
              onChange={(e) => setDraft(e.target.value)}
              type="number"
              value={draft}
            />
            <p className="text-xs text-muted-foreground">
              ${MIN_USD}–${MAX_USD.toLocaleString("en-US")} in whole dollars. 1 USD = 100 credits.
            </p>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => setOpen(false)} size="sm" variant="secondary">
            Cancel
          </Button>
          <Button
            disabled={!isValid || topUp.isPending}
            onClick={() => void startCheckout()}
            size="sm"
          >
            {topUp.isPending ? "Redirecting…" : "Continue to payment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
