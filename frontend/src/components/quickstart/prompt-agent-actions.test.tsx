// @vitest-environment jsdom
// covers: console-logic

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PromptAgentActions } from "@/components/quickstart/prompt-agent-actions";

const prompt = "Set me up on Overmind\n\nnow";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PromptAgentActions", () => {
  it.each([
    [undefined],
    [null],
    ["coding_agent"],
    ["opencode"],
  ])("offers a labeled copy and no app link for agent %s", (defaultAgent) => {
    render(<PromptAgentActions defaultAgent={defaultAgent} prompt={prompt} />);
    expect(screen.getByRole("button", { name: "Copy prompt" }).textContent).toBe("Copy prompt");
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByRole("button", { name: "Choose coding agent" })).toBeTruthy();
  });

  it.each([
    ["claude", "Open in Claude Code", "claude://code/new?q="],
    ["codex", "Open in Codex", "codex://new?prompt="],
    ["cursor", "Open in Cursor", "cursor://anysphere.cursor-deeplink/prompt?text="],
  ])("links a known %s session to its app and keeps copy", (defaultAgent, name, url) => {
    render(<PromptAgentActions defaultAgent={defaultAgent} prompt={prompt} />);
    expect(screen.getByRole("link", { name }).getAttribute("href")).toBe(
      url + encodeURIComponent(prompt)
    );
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeTruthy();
  });

  it("offers only copy on a phone", () => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"
    );
    render(<PromptAgentActions defaultAgent="codex" prompt={prompt} />);
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button", { name: "Choose coding agent" })).toBeNull();
  });
});

it("applies an agent choice when persistence is unavailable", () => {
  render(<PromptAgentActions defaultAgent="claude" prompt={prompt} />);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("Quota exceeded", "QuotaExceededError");
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "Choose coding agent" }), { key: "Enter" });
  fireEvent.click(screen.getByRole("menuitem", { name: "Codex" }));
  expect(screen.getByRole("link", { name: "Open in Codex" }).getAttribute("href")).toBe(
    `codex://new?prompt=${encodeURIComponent(prompt)}`
  );
  fireEvent(window, new StorageEvent("storage", { key: "preferred-coding-agent" }));
});
