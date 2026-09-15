import { describe, expect, it } from "vitest";

import { humanizeKey, sentenceCase } from "./label-case";

describe("sentenceCase", () => {
  it("capitalises only the first word", () => {
    expect(sentenceCase("expected output")).toBe("Expected output");
    expect(sentenceCase("Expected Output")).toBe("Expected output");
    expect(sentenceCase("EXPECTED OUTPUT")).toBe("Expected output");
    expect(sentenceCase("not traced")).toBe("Not traced");
  });

  it("preserves acronyms wherever they land, including first position", () => {
    expect(sentenceCase("trace id")).toBe("Trace ID");
    expect(sentenceCase("id")).toBe("ID");
    expect(sentenceCase("json payload")).toBe("JSON payload");
    expect(sentenceCase("ok")).toBe("OK");
    expect(sentenceCase("llm judge")).toBe("LLM judge");
    expect(sentenceCase("p95 latency")).toBe("P95 latency");
    expect(sentenceCase("open pr")).toBe("Open PR");
  });

  it("preserves proper nouns in their canonical casing", () => {
    expect(sentenceCase("openai key")).toBe("OpenAI key");
    expect(sentenceCase("github app")).toBe("GitHub app");
    expect(sentenceCase("lora rank")).toBe("LoRA rank");
  });

  it("matches preserved words through surrounding punctuation", () => {
    expect(sentenceCase("payload (json)")).toBe("Payload (JSON)");
    expect(sentenceCase("id:")).toBe("ID:");
  });

  it("normalises whitespace and tolerates empty input", () => {
    expect(sentenceCase("  spaced   out  ")).toBe("Spaced out");
    expect(sentenceCase("")).toBe("");
    expect(sentenceCase("   ")).toBe("");
  });
});

describe("humanizeKey", () => {
  it("splits snake_case, kebab-case and camelCase", () => {
    expect(humanizeKey("expected_output")).toBe("Expected output");
    expect(humanizeKey("ready_with_warnings")).toBe("Ready with warnings");
    expect(humanizeKey("multi-capability")).toBe("Multi capability");
    expect(humanizeKey("baseModel")).toBe("Base model");
  });

  it("keeps acronyms intact across the split", () => {
    expect(humanizeKey("trace_id")).toBe("Trace ID");
    expect(humanizeKey("traceId")).toBe("Trace ID");
    expect(humanizeKey("api_url")).toBe("API URL");
    expect(humanizeKey("llm_judge_score")).toBe("LLM judge score");
  });

  it("splits an acronym run from a following word", () => {
    expect(humanizeKey("JSONPayload")).toBe("JSON payload");
  });

  it("passes single words through", () => {
    expect(humanizeKey("input")).toBe("Input");
    expect(humanizeKey("persona")).toBe("Persona");
  });
});
