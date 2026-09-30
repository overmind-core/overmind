import { createRoot } from "react-dom/client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";

import api from "./src/client";
import { DatasetChat } from "./src/components/datasets/notebook/chat";
import { workshopFundingKey } from "./src/hooks/use-workshop-funding";
import type { WorkshopFunding } from "./src/openapi";
import "./src/styles.css";

let funding: WorkshopFunding = {
  accountId: "fixture",
  accounts: [
    {
      connected: true,
      email: "fixture@example.invalid",
      id: "fixture",
      label: "Fixture account",
      planEnabled: true,
    },
  ],
  enabled: true,
  fundingSource: "platform",
  model: "",
  usageUrl: "https://chatgpt.com/#settings/Usage",
};
api.chatgpt.chatgptRetrieve = async () => funding;
api.chatgpt.chatgptModelsList = async () => [
  { id: "gpt-fixture-small", name: "Small model" },
  { id: "gpt-fixture-large", name: "Large model" },
];
api.chatgpt.chatgptPartialUpdate = async ({ patchedWorkshopFundingRequestRequest } = {}) => {
  await new Promise((resolve) => setTimeout(resolve, 600));
  funding = { ...funding, ...patchedWorkshopFundingRequestRequest };
  return funding;
};
const cache = new QueryClient();
cache.setQueryData(workshopFundingKey, funding);
function Preview() {
  return (
    <QueryClientProvider client={cache}>
      <main className="mx-auto flex h-dvh max-w-4xl flex-col bg-background text-foreground">
        <p className="p-4 text-sm text-muted-foreground">
          Composer verification · fixture account and models
        </p>
        <DatasetChat
          busy={false}
          cells={[]}
          initialRequest="Inspect the source rows"
          live={null}
          onAccept={() => {}}
          onChooseIntent={() => undefined}
          onDiscard={() => {}}
          onSelect={() => {}}
          onSend={async () => true}
          renderCell={() => null}
          state="idle"
          turns={[]}
        />
      </main>
    </QueryClientProvider>
  );
}
const route = createRootRoute({ component: Preview });
const router = createRouter({
  history: createMemoryHistory({ initialEntries: ["/"] }),
  routeTree: route,
});
createRoot(document.getElementById("root")!).render(<RouterProvider router={router} />);
