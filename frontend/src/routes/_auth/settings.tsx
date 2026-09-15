import { useCallback, useEffect } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import * as z from "zod";

import { AccountBand } from "@/components/billing/account-band";
import { BillingLedgerTable } from "@/components/billing/billing-ledger-table";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import {
  billingLedgerQueryKey,
  subscriptionQueryKey,
  useCommercialBilling,
} from "@/hooks/use-subscription";
import { PROSE, TITLE } from "@/lib/typography";

const settingsSearchSchema = z.object({
  billing: z.string().optional(),
  projectId: z.string().optional(),
});

export const Route = createFileRoute("/_auth/settings")({
  component: SettingsPage,
  validateSearch: settingsSearchSchema,
});

function SettingsPage() {
  const settingsSearch = Route.useSearch();
  const navigate = Route.useNavigate();
  const queryClient = useQueryClient();
  const { enabled: commercial } = useCommercialBilling();

  const autoCheckout = settingsSearch.billing === "checkout";

  const handleAutoCheckoutStarted = useCallback(() => {
    navigate({
      replace: true,
      search: (prev) => ({ ...prev, billing: undefined }),
    });
  }, [navigate]);

  useEffect(() => {
    // Stripe returns `success` from plan checkout and `topup` from a credit
    // purchase. The webhook, not this redirect, writes the balance and plan.
    const billing = settingsSearch.billing;
    if (billing !== "success" && billing !== "topup") return;
    toast.success(
      billing === "topup"
        ? "Payment received. Your credits will appear shortly."
        : "Payment received. Your plan will update shortly."
    );
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKey });
    void queryClient.invalidateQueries({ queryKey: billingLedgerQueryKey });
    navigate({
      replace: true,
      search: (prev) => ({ ...prev, billing: undefined }),
    });
  }, [settingsSearch.billing, navigate, queryClient]);

  return (
    <PageShell
      header={
        <PageHeader
          description={
            commercial ? "Manage your plan, credits and usage." : "Usage across all projects."
          }
          icon={<Icon.settings aria-hidden className="size-6 shrink-0" />}
          title="Settings"
        />
      }
      variant="scroll"
    >
      <section aria-labelledby="account-heading" className="space-y-6">
        <div className="space-y-4">
          <div>
            <h2 className={TITLE.section} id="account-heading">
              Account
            </h2>
            <p className={`${PROSE} mt-1 text-sm text-muted-foreground`}>
              {commercial
                ? "Plan, credits and usage. Shared across all projects."
                : "Shared across all projects."}
            </p>
          </div>
          <AccountBand
            autoCheckout={autoCheckout}
            onAutoCheckoutStarted={handleAutoCheckoutStarted}
          />
        </div>
        <BillingLedgerTable />
      </section>
    </PageShell>
  );
}
