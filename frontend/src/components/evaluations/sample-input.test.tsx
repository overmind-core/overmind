// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { EvalSampleIO } from "@/openapi";
import { SampleInput } from "./sample-input";

afterEach(cleanup);

const io: EvalSampleIO = {
  input: {
    messages: [
      { content: "Canonical prompt", role: "system" },
      { content: "Evidence", role: "user" },
    ],
    tools: [],
  },
  inputSource: "recorded",
  output: "Generated answer",
  outputMessages: [],
  reference: "Reference answer",
  truncated: false,
};

describe("SampleInput", () => {
  it("shows the captured request without the generated or expected answer", () => {
    render(<SampleInput io={io} viewMode="formatted" />);
    expect(screen.getByText("Initial model input")).toBeTruthy();
    expect(screen.getByText("Canonical prompt")).toBeTruthy();
    expect(screen.getByText("Evidence")).toBeTruthy();
    expect(screen.queryByText("Generated answer")).toBeNull();
    expect(screen.queryByText("Reference answer")).toBeNull();
  });

  it("labels historical dataset input as not an exact request", () => {
    render(
      <SampleInput
        io={{ ...io, input: { documents: ["passport"] }, inputSource: "dataset" }}
        viewMode="formatted"
      />
    );
    expect(screen.getByText("Dataset input")).toBeTruthy();
    expect(screen.getByText(/exact model request was not captured/)).toBeTruthy();
    expect(screen.getByText(/passport/)).toBeTruthy();
  });

  it("shows tool calls and definitions rather than only message content", () => {
    const input = {
      messages: [
        {
          content: null,
          role: "assistant",
          tool_calls: [{ arguments: { country: "GB" }, name: "screen_entity" }],
        },
      ],
      tools: [{ name: "screen_entity", parameters: { type: "object" } }],
    };
    render(<SampleInput io={{ ...io, input }} viewMode="formatted" />);
    expect(screen.getByText("Tool calls")).toBeTruthy();
    expect(screen.getByText("Tools · 1")).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Evaluation input" })).getAllByText(/screen_entity/)
    ).toHaveLength(2);
  });

  it("does not invent missing inputs and reports truncation", () => {
    render(
      <SampleInput
        io={{ ...io, input: null, inputSource: "unavailable", truncated: true }}
        viewMode="raw"
      />
    );
    expect(screen.getByText(/pinned dataset row is unavailable/)).toBeTruthy();
    expect(screen.getByText("Stored payload was truncated.")).toBeTruthy();
  });
});
