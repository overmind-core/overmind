import { describe, expect, it } from "vitest";

import { dataOnboardingPrompt } from "./data-onboarding-prompt";

describe("data onboarding handoff", () => {
  it.each([
    "https://api.overmindlab.ai",
    "https://api-staging.overmindlab.ai",
    "https://customer.example",
    "http://localhost:8000",
  ])("pins the uploaded dataset and chosen endpoint %s", (url) => {
    const prompt = dataOnboardingPrompt("codex", "test-key", `${url}/`, "project-1", "dataset-1");
    expect(prompt).toContain(`MCP: ${url}/api/mcp/`);
    expect(prompt).toContain("Project ID: project-1");
    expect(prompt).toContain("Dataset ID: dataset-1");
    expect(prompt).toContain(`connection check --project-id project-1 --api-url ${url} --json`);
    expect(prompt).toContain("Start with the standard data exploration workflow");
    expect(prompt).toContain("Do not assume conversational SFT");
    expect(prompt).toContain("chat_train");
    expect(prompt).toContain("decision_train");
    expect(prompt).toContain("Never turn soft targets into argmax text");
    expect(prompt).toContain("chosen training representation and its evidence");
    expect(prompt).toContain("Do not create another project or re-upload the original files");
    expect(prompt).toContain("Do not launch training, evaluation or inference");
    expect(prompt).not.toContain("overmind init");
    expect(prompt).not.toContain("overmind sync");
  });
});
