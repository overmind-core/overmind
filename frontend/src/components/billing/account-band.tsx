import { ResponseError } from "@/client";
import { TopUpDialog } from "@/components/billing/top-up-dialog";
import { CreditsAmount } from "@/components/ui/credits";
import { Skeleton } from "@/components/ui/skeleton";
import { useBillingAccount, useCommercialBilling } from "@/hooks/use-billing-account";
import { cn } from "@/lib/utils";

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ResponseError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
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

function Amount({ usd, raw }: { usd: number; raw: string }) {
  return (
    <span className="flex h-6 items-center text-sm font-medium tabular-nums text-foreground">
      {Number.isFinite(usd) ? <CreditsAmount usd={usd} /> : raw}
    </span>
  );
}

export function AccountBand({ className }: { className?: string }) {
  const query = useBillingAccount();
  const { enabled: commercial, ready } = useCommercialBilling();
  const account = query.data;

  if (query.isLoading || !ready) {
    return (
      <div className={cn("rounded-md border border-border bg-card px-4 py-3", className)}>
        <div aria-busy="true" aria-label="Loading credits" role="status">
          <span className="sr-only">Loading credits</span>
          <div aria-hidden="true" className="flex items-center justify-between gap-4">
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-3 w-1/2" />
            </div>
            <Skeleton className="h-8 w-24" />
          </div>
        </div>
      </div>
    );
  }

  if (query.error || !account) {
    return (
      <div className={cn("rounded-md border border-border bg-card px-4 py-3", className)}>
        <p className="text-sm text-muted-foreground">
          {errorMessage(query.error, "Failed to load credits")}
        </p>
      </div>
    );
  }

  const spent = <Amount raw={account.creditsSpentUsd} usd={Number(account.creditsSpentUsd)} />;

  return (
    <div className={cn("rounded-md border border-border bg-card", className)}>
      <div className="flex flex-col divide-y divide-border/70 sm:flex-row sm:items-stretch sm:divide-x sm:divide-y-0">
        {commercial ? (
          <Fact label="Credits">
            <Amount raw={account.creditsUsd} usd={Number(account.creditsUsd)} />
          </Fact>
        ) : null}
        <Fact label="Spent">{spent}</Fact>
        {commercial ? (
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 sm:ml-auto">
            <TopUpDialog />
          </div>
        ) : null}
      </div>
    </div>
  );
}
