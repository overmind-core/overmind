import { describe, expect, it } from "vitest";

import { buildCurlSnippet, buildPythonSnippet, inferenceBaseUrl } from "./api-snippet-dialog";

describe("inference snippets", () => {
  it("builds the public /api/v1 base URL", () => {
    expect(inferenceBaseUrl("https://api.example.com")).toBe("https://api.example.com/api/v1");
    expect(inferenceBaseUrl("https://api.example.com/")).toBe("https://api.example.com/api/v1");
  });

  it("embeds model id and key in curl", () => {
    const snip = buildCurlSnippet({
      apiKey: "sk-test",
      baseUrl: "https://api.example.com/api/v1",
      modelId: "ft-abc",
    });
    expect(snip).toContain("https://api.example.com/api/v1/chat/completions");
    expect(snip).toContain("Bearer sk-test");
    expect(snip).toContain('"model": "ft-abc"');
  });

  it("embeds model id and key in python", () => {
    const snip = buildPythonSnippet({
      apiKey: "sk-test",
      baseUrl: "https://api.example.com/api/v1",
      modelId: "ft-abc",
    });
    expect(snip).toContain('base_url="https://api.example.com/api/v1"');
    expect(snip).toContain('api_key="sk-test"');
    expect(snip).toContain('model="ft-abc"');
  });
});
