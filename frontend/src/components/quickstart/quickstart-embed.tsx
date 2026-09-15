import desktopMonitorIcon from "@/assets/desktop-monitor.svg";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { EmptyState } from "@/components/ui/empty-state";

const CapabilitiesIcon = (_props: { className?: string }) => (
  <span className="mb-4 flex size-14 items-center justify-center rounded-md border border-border bg-muted">
    <img
      alt=""
      aria-hidden="true"
      className="size-8 [image-rendering:pixelated] dark:invert"
      src={desktopMonitorIcon}
    />
  </span>
);

export function QuickstartEmbed({ projectId }: { projectId: string }) {
  return (
    <>
      <EmptyState
        className="pb-4"
        description="Open your repository in a coding agent and paste the onboarding prompt. The agent runs init sync first; capability scan follows in the skill."
        icon={CapabilitiesIcon}
        size="section"
        title="No capabilities yet"
      />
      <div className="mx-auto w-full max-w-2xl overflow-hidden rounded-md border border-border p-4">
        <OnboardWithAiPanel projectId={projectId} />
      </div>
    </>
  );
}
