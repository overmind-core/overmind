import { describe, expect, it } from "vitest";

import { buildCurlSnippet, buildPythonSnippet, inferenceBaseUrl } from "@/lib/inference-snippets";

describe("inference snippets", () => {
  it("builds the public /api/v1 base URL", () => {
    expect(inferenceBaseUrl("https://api.example.com")).toBe("https://api.example.com/api/v1");
    expect(inferenceBaseUrl("https://api.example.com/")).toBe("https://api.example.com/api/v1");
  });

  it("reads the project key from the environment in curl", () => {
    const snip = buildCurlSnippet({
      baseUrl: "https://api.example.com/api/v1",
      modelId: "ft-abc",
    });
    expect(snip).toContain("https://api.example.com/api/v1/chat/completions");
    expect(snip).toContain("Bearer $OVERMIND_API_KEY");
    expect(snip).toContain('"model": "ft-abc"');
  });

  it("reads the project key from the environment in Python", () => {
    const snip = buildPythonSnippet({
      baseUrl: "https://api.example.com/api/v1",
      modelId: "ft-abc",
    });
    expect(snip).toContain('base_url="https://api.example.com/api/v1"');
    expect(snip).toContain('api_key=os.environ["OVERMIND_API_KEY"]');
    expect(snip).toContain('model="ft-abc"');
  });
});
