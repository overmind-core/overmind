// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreateEvaluatorDialog } from "./create-evaluator-dialog";

vi.mock("@/hooks/use-evaluations", () => ({
  useAuthorJudgeEvaluatorMutation: () => ({ isPending: false }),
  useEditJudgeEvaluatorMutation: () => ({ isPending: false }),
  useEvalSetsQuery: () => ({ data: { results: [] } }),
  useGenerateEvaluatorPromptMutation: () => ({ isPending: false }),
  useModelCatalogQuery: () => ({ data: { defaults: { judgeModel: "", judgeModels: [] } } }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [] } }),
}));
vi.mock("@/hooks/use-behaviours", () => ({
  useBehavioursQuery: () => ({ data: { results: [] } }),
}));
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  getProviderIcon: () => undefined,
  ProviderLogo: () => null,
}));

afterEach(cleanup);

describe("Evaluator decision engine", () => {
  it("keeps Jev opt-in when the creation dialog is reopened", () => {
    render(<CreateEvaluatorDialog projectId="project" />);
    for (let attempt = 0; attempt < 2; attempt++) {
      fireEvent.click(screen.getByRole("button", { name: "New evaluator" }));
      expect(screen.getByRole("combobox", { name: "Decision engine" }).textContent).toBe(
        "Generative"
      );
      expect(screen.queryByLabelText("Confidence floor")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
    }
  });
});
