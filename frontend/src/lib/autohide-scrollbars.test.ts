// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installAutohideScrollbars } from "./autohide-scrollbars";

describe("autohide scrollbars", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installAutohideScrollbars();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  const scroll = (el: Element) => el.dispatchEvent(new Event("scroll", { bubbles: false }));

  it("marks the scrolled element and clears it once idle", () => {
    const el = document.createElement("div");
    document.body.append(el);

    scroll(el);
    expect(el.hasAttribute("data-scrolling")).toBe(true);

    vi.advanceTimersByTime(699);
    expect(el.hasAttribute("data-scrolling")).toBe(true);

    vi.advanceTimersByTime(1);
    expect(el.hasAttribute("data-scrolling")).toBe(false);
  });

  it("keeps the mark through a continuous scroll", () => {
    const el = document.createElement("div");
    document.body.append(el);

    scroll(el);
    vi.advanceTimersByTime(500);
    scroll(el);
    vi.advanceTimersByTime(500);
    expect(el.hasAttribute("data-scrolling")).toBe(true);
  });

  it("marks the root element for document-level scrolls", () => {
    document.dispatchEvent(new Event("scroll"));
    expect(document.documentElement.hasAttribute("data-scrolling")).toBe(true);
  });
});
