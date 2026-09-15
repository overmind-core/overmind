import { useEffect, useRef } from "react";

import { format } from "date-fns";
import { toast } from "sonner";

import { ResponseError } from "@/client";
import { TopUpDialog } from "@/components/billing/top-up-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CreditsAmount } from "@/components/ui/credits";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import {
  useBuyPlan,
  useCancelSubscription,
  useCommercialBilling,
  useRenewSubscription,
  useSubscriptionQuery,
} from "@/hooks/use-subscription";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import { PlanEnum, type PlanUsage, type PlanUsageUnit, type Subscription } from "@/openapi";

function formatDate(value: Date | null | undefined): string | null {
  if (!value) return null;
  return format(value, "d MMM yyyy");
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ResponseError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function planBadge(sub: Subscription) {
  if (sub.plan === PlanEnum.pro && sub.cancelAtPeriodEnd) {
    return <Badge variant="warning">Cancels soon</Badge>;
  }
  if (sub.plan === PlanEnum.pro) {
    return <Badge variant="success">Pro</Badge>;
  }
  if (sub.status === "past_due") {
    return <Badge variant="warning">Past due</Badge>;
  }
  return <Badge variant="neutral">Free</Badge>;
}

function SharedActions() {
  return (
    <>
      <TopUpDialog />
      <Button asChild size="sm" variant="secondary">
        <a href={PUBLIC_PRICING_URL} rel="noreferrer" target="_blank">
          <Icon.forward />
          View plans
        </a>
      </Button>
    </>
  );
}

function PlanActions({ sub }: { sub: Subscription }) {
  const buy = useBuyPlan();
  const cancel = useCancelSubscription();
  const renew = useRenewSubscription();

  async function startCheckout() {
    try {
      const res = await buy.mutateAsync();
      window.location.href = res.checkoutUrl;
    } catch (err) {
      notify.error(err, "Checkout failed");
    }
  }

  async function onCancel() {
    try {
      await cancel.mutateAsync();
      toast.success("Pro will cancel at the end of the billing period.");
    } catch (err) {
      notify.error(err, "Could not cancel plan");
    }
  }

  async function onRenew() {
    try {
      await renew.mutateAsync();
      toast.success("Auto-renewal resumed.");
    } catch (err) {
      notify.error(err, "Could not renew plan");
    }
  }

  if (sub.plan === PlanEnum.pro && sub.cancelAtPeriodEnd) {
    return (
      <>
        <Button disabled={renew.isPending} onClick={() => void onRenew()} size="sm">
          {renew.isPending ? "Resuming…" : "Renew plan"}
        </Button>
        <SharedActions />
      </>
    );
  }

  if (sub.plan === PlanEnum.pro) {
    return (
      <>
        <ConfirmDialog
          confirmLabel="Cancel plan"
          description={
            <>
              <p>
                {sub.endDate
                  ? `You'll keep Pro access and remaining credits until ${formatDate(sub.endDate)}. Auto-renewal stops after that.`
                  : "You'll keep Pro access until the end of the current billing period."}
              </p>
              <p className="mt-2">
                When Pro ends, projects you created revert to a single seat (you alone). Teammates
                will lose access to those projects.
              </p>
            </>
          }
          destructive
          isPending={cancel.isPending}
          onConfirm={onCancel}
          title="Cancel Pro?"
          trigger={
            <Button disabled={cancel.isPending} size="sm" variant="secondary">
              <Icon.close />
              Cancel plan
            </Button>
          }
        />
        <SharedActions />
      </>
    );
  }

  return (
    <>
      <Button disabled={buy.isPending} onClick={() => void startCheckout()} size="sm">
        {buy.isPending ? "Redirecting…" : "Buy Pro"}
      </Button>
      <SharedActions />
    </>
  );
}

function Fact({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 px-4 py-3", className)}>
      <p className="pixel-label mb-1.5 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

const QUOTA_ROWS: { key: keyof PlanUsage; label: string; monthly?: boolean }[] = [
  { key: "projects", label: "Projects" },
  { key: "optimizeRuns", label: "Optimise runs", monthly: true },
  { key: "trainingJobs", label: "Training jobs", monthly: true },
  { key: "deployJobs", label: "Deploy jobs", monthly: true },
];

function formatUnit(unit: PlanUsageUnit): string {
  const used = unit.used ?? 0;
  if (unit.limit == null) return `${used} / Unlimited`;
  return `${used} / ${unit.limit}`;
}

function atCap(unit: PlanUsageUnit): boolean {
  return unit.limit != null && (unit.used ?? 0) >= unit.limit;
}

function QuotaStat({
  label,
  title,
  value,
  warning,
}: {
  label: string;
  title?: string;
  value: string;
  warning?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5" title={title}>
      <span className="pixel-label text-xs text-muted-foreground">{label}</span>
      <span
        className={cn(
          "text-sm font-medium leading-none tabular-nums",
          warning ? "text-warning" : "text-foreground"
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function AccountBand({
  autoCheckout = false,
  className,
  onAutoCheckoutStarted,
}: {
  /** Marketing "Get Pro" lands on `/settings?billing=checkout`; start Stripe Checkout once. */
  autoCheckout?: boolean;
  className?: string;
  onAutoCheckoutStarted?: () => void;
}) {
  const query = useSubscriptionQuery();
  const { enabled: commercial, ready } = useCommercialBilling();
  const buy = useBuyPlan();
  const autoCheckoutStarted = useRef(false);

  useEffect(() => {
    if (!commercial) return;
    if (!autoCheckout || autoCheckoutStarted.current || query.isLoading || !query.data) return;
    if (query.data.plan === PlanEnum.pro && !query.data.cancelAtPeriodEnd) {
      autoCheckoutStarted.current = true;
      onAutoCheckoutStarted?.();
      toast.message("You already have an active Pro subscription.");
      return;
    }
    if (query.data.plan === PlanEnum.pro && query.data.cancelAtPeriodEnd) {
      // Cancelling Pro resumes via Renew — do not open a second Checkout Session.
      autoCheckoutStarted.current = true;
      onAutoCheckoutStarted?.();
      return;
    }

    autoCheckoutStarted.current = true;
    onAutoCheckoutStarted?.();
    void (async () => {
      try {
        const res = await buy.mutateAsync();
        window.location.href = res.checkoutUrl;
      } catch (err) {
        notify.error(err, "Checkout failed");
      }
    })();
  }, [autoCheckout, buy, commercial, onAutoCheckoutStarted, query.data, query.isLoading]);

  const sub = query.data;

  if (query.isLoading || buy.isPending || !ready) {
    return (
      <div className={cn("rounded-md border border-border bg-card px-4 py-3", className)}>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner size="sm" />
          {buy.isPending
            ? "Redirecting to Stripe…"
            : commercial
              ? "Loading plan…"
              : "Loading usage…"}
        </div>
      </div>
    );
  }

  if (query.error || !sub) {
    return (
      <div className={cn("rounded-md border border-border bg-card px-4 py-3", className)}>
        <p className="text-sm text-destructive">
          {errorMessage(query.error, "Failed to load subscription")}
        </p>
      </div>
    );
  }

  const end = formatDate(sub.endDate);
  const start = formatDate(sub.startDate);
  const creditsN = Number(sub.creditsUsd);
  const spentN = Number(sub.creditsSpentUsd);
  const usage = sub.usage;

  if (!commercial) {
    return (
      <div className={cn("rounded-md border border-border bg-card", className)}>
        <div className="flex flex-col divide-y divide-border/70 sm:flex-row sm:items-stretch sm:divide-x sm:divide-y-0">
          <Fact label="Spent">
            <span className="flex h-6 items-center text-sm font-medium tabular-nums text-foreground">
              {Number.isFinite(spentN) ? <CreditsAmount usd={spentN} /> : sub.creditsSpentUsd}
            </span>
          </Fact>
        </div>
      </div>
    );
  }

  return (
    <div className={cn("rounded-md border border-border bg-card", className)}>
      <div className="flex flex-col divide-y divide-border/70 sm:flex-row sm:items-stretch sm:divide-x sm:divide-y-0">
        <Fact label="Plan">
          <span className="flex h-6 items-center gap-2 text-sm">
            {planBadge(sub)}
            {end ? (
              <span className="text-muted-foreground">
                {sub.cancelAtPeriodEnd ? `until ${end}` : `renews ${end}`}
              </span>
            ) : null}
          </span>
        </Fact>
        {start ? (
          <Fact label="Started">
            <span className="flex h-6 items-center text-sm text-foreground">{start}</span>
          </Fact>
        ) : null}
        <Fact label="Credits">
          <span className="flex h-6 items-center text-sm font-medium tabular-nums text-foreground">
            {Number.isFinite(creditsN) ? <CreditsAmount usd={creditsN} /> : sub.creditsUsd}
          </span>
        </Fact>
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 sm:ml-auto">
          <PlanActions sub={sub} />
        </div>
      </div>

      {sub.plan === PlanEnum.pro && sub.cancelAtPeriodEnd ? (
        <p className="border-t border-border/70 px-4 py-2 text-sm text-warning">
          Cancellation scheduled{end ? ` — access ends ${end}` : ""}. Renew to keep Pro.
        </p>
      ) : null}

      {usage ? (
        <div className="flex flex-wrap items-start gap-x-10 gap-y-4 border-t border-border/70 px-4 py-3">
          {QUOTA_ROWS.map(({ key, label, monthly }) => {
            const unit = usage[key];
            return (
              <QuotaStat
                key={key}
                label={label}
                title={
                  monthly ? "Resets on the 1st of the month (UTC)" : "Lifetime cap on the Free plan"
                }
                value={formatUnit(unit)}
                warning={atCap(unit)}
              />
            );
          })}
          <QuotaStat
            label="Seats / project"
            value={usage.seats.limit == null ? "Unlimited" : String(usage.seats.limit)}
          />
        </div>
      ) : null}
    </div>
  );
}
