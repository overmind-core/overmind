import { useEffect, useRef } from "react";

import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import * as z from "zod";

import { AgentFeedbackSetting } from "@/components/agent-feedback";
import { AccountBand } from "@/components/billing/account-band";
import { BillingLedgerTable } from "@/components/billing/billing-ledger-table";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useCommercialBilling, useConfirmTopUp } from "@/hooks/use-billing-account";
import { notify } from "@/lib/notify";
import { PROSE, TITLE } from "@/lib/typography";

const settingsSearchSchema = z.object({
  billing: z.string().optional(),
  projectId: z.string().optional(),
  session_id: z.string().optional(),
});

export const Route = createFileRoute("/_auth/settings")({
  component: SettingsPage,
  validateSearch: settingsSearchSchema,
});

function SettingsPage() {
  const settingsSearch = Route.useSearch();
  const navigate = Route.useNavigate();
  const { enabled: commercial } = useCommercialBilling();
  const { mutate: confirmTopUp } = useConfirmTopUp();
  const confirmed = useRef<string | null>(null);

  useEffect(() => {
    const { billing, session_id: sessionId } = settingsSearch;
    if (!billing) return;
    navigate({
      replace: true,
      search: (prev) => ({ ...prev, billing: undefined, session_id: undefined }),
    });
    if (billing !== "topup" || !sessionId || confirmed.current === sessionId) return;
    confirmed.current = sessionId;
    confirmTopUp(sessionId, {
      onError: (err) => notify.error(err, "Could not confirm the payment"),
      onSuccess: ({ paid }) => {
        toast.success(paid ? "Credits added." : "Payment pending. Credits follow when it clears.");
      },
    });
  }, [settingsSearch, navigate, confirmTopUp]);

  return (
    <PageShell
      header={
        <PageHeader
          description={commercial ? "Credits and usage." : "Usage across all projects."}
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
                ? "Credits and usage. Shared across all projects."
                : "Shared across all projects."}
            </p>
          </div>
          <AccountBand />
        </div>
        <BillingLedgerTable />
      </section>
      <section aria-labelledby="privacy-heading" className="mt-10 space-y-4">
        <h2 className={TITLE.section} id="privacy-heading">
          Privacy
        </h2>
        <AgentFeedbackSetting />
      </section>
    </PageShell>
  );
}
