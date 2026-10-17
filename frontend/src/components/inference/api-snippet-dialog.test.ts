import { describe, expect, it } from "vitest";

import {
  buildCurlSnippet,
  buildDecisionSnippets,
  buildPythonSnippet,
  inferenceBaseUrl,
} from "@/lib/inference-snippets";

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

  it("points a decision model at the TypeSafe systemone SDK", () => {
    const snip = buildDecisionSnippets(
      "ft-abc",
      {
        kind: "choice",
        options: ["no", "yes"],
        question: "Is it ready?",
        state: "The service is up.",
      },
      "https://api.example.com"
    );
    expect(snip.curl).toContain("https://api.example.com/api/v1/systemone");
    expect(snip.curl).toContain('"model": "ft-abc"');
    expect(snip.python).toContain('base_url="https://api.example.com/api"');
    expect(snip.python).toContain("from typesafe_sdk import Choice, TypeSafeClient");
    expect(snip.python).toContain('model="ft-abc"');
    expect(snip.python).not.toContain("inference-requests");
    expect(snip.python).toContain('"no": None');
  });

  it("copies choice descriptions from the question into criteria", () => {
    const snip = buildDecisionSnippets("ft-abc", {
      kind: "choice",
      options: ["low", "high"],
      question:
        "Which risk applies?\nlow: Little evidence of publisher risk.\nhigh: Strong evidence of publisher risk.",
      state: "An email.",
    });
    expect(snip.python).toContain('"low": "Little evidence of publisher risk."');
    expect(snip.curl).toContain('"high": "Strong evidence of publisher risk."');
  });
});
