import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function QuickstartEmbed({ projectId }: { projectId: string }) {
  return (
    <Card className="mx-auto w-full max-w-2xl overflow-hidden p-4">
      <Tabs defaultValue="get-started">
        <TabsList aria-label="Agent onboarding">
          <TabsTrigger value="get-started">Get started</TabsTrigger>
        </TabsList>
        <TabsContent className="mt-4" value="get-started">
          <OnboardWithAiPanel projectId={projectId} />
        </TabsContent>
      </Tabs>
    </Card>
  );
}
