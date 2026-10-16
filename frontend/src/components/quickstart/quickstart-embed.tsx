import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { Card } from "@/components/ui/card";

export function QuickstartEmbed({ projectId }: { projectId: string }) {
  return (
    <section aria-labelledby="agent-onboarding-title" className="w-full max-w-2xl">
      <h2
        className="relative z-10 -mb-px ml-4 w-fit rounded-t-md border border-b-0 border-border bg-card px-4 py-2 text-sm font-medium"
        id="agent-onboarding-title"
      >
        Get started
      </h2>
      <Card className="p-4">
        <OnboardWithAiPanel projectId={projectId} />
      </Card>
    </section>
  );
}
