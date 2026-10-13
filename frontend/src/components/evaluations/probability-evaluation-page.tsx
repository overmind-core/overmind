import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Failure } from "@/components/model-workflows/common";
import { ComparisonDetail } from "@/components/model-workflows/decision-comparisons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";

export function ProbabilityEvaluationPage({ id }: { id: string }) {
  const query = useQuery({
    queryFn: () => apiClient.nativeEvaluations.nativeEvaluationsRetrieve({ id }),
    queryKey: ["decision-comparison", id],
    refetchInterval: (q) =>
      ["queued", "running", "preparing", "waiting_for_checkpoint"].includes(
        q.state.data?.state ?? ""
      )
        ? 5000
        : false,
  });
  const plan = query.data;
  return (
    <PageShell header={<PageHeader title="Evaluations" />} variant="full">
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto pb-4">
        <Failure error={query.error} />
        {query.isPending && <LoadingState label="Loading evaluation" />}
        {plan && <ComparisonDetail key={plan.id} plan={plan} />}
      </div>
    </PageShell>
  );
}
