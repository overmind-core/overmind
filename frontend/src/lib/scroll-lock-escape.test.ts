// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import { escapeScrollLock } from "./scroll-lock-escape";

const teardown: Array<() => void> = [];

afterEach(() => {
  for (const fn of teardown.splice(0)) {
    fn();
  }
  document.body.innerHTML = "";
});

function installScrollLock(lockedSubtree: HTMLElement) {
  const handler = (event: Event) => {
    const target = event.target as Node | null;
    const originatesInsideDialog = target !== null && lockedSubtree.contains(target);
    if (!originatesInsideDialog && event.cancelable) {
      event.preventDefault();
    }
  };
  // Matches react-remove-scroll: document-level, bubble phase, non-passive.
  document.addEventListener("wheel", handler, { passive: false });
  teardown.push(() => document.removeEventListener("wheel", handler));
}

function buildPortaledList(): HTMLDivElement {
  const dialog = document.createElement("div");
  document.body.appendChild(dialog);
  installScrollLock(dialog);

  // Popover content portals to <body>, a sibling of the locked subtree.
  const popover = document.createElement("div");
  document.body.appendChild(popover);
  const list = document.createElement("div");
  popover.appendChild(list);
  return list;
}

function dispatchWheel(target: HTMLElement): boolean {
  const event = new Event("wheel", { bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event.defaultPrevented;
}

describe("escapeScrollLock", () => {
  it("reproduces the bug: without the guard, the dialog scroll-lock cancels wheel scroll on portaled content", () => {
    const list = buildPortaledList();
    expect(dispatchWheel(list)).toBe(true);
  });

  it("fixes it: the guard stops propagation so the document listener never cancels the scroll", () => {
    const list = buildPortaledList();
    const cleanup = escapeScrollLock(list);
    teardown.push(() => cleanup?.());

    expect(dispatchWheel(list)).toBe(false);
  });

  it("still lets the element's own wheel handler (native scroll path) run", () => {
    const list = buildPortaledList();
    const cleanup = escapeScrollLock(list);
    teardown.push(() => cleanup?.());

    let elementSawEvent = false;
    list.addEventListener("wheel", () => {
      elementSawEvent = true;
    });

    expect(dispatchWheel(list)).toBe(false);
    expect(elementSawEvent).toBe(true);
  });

  it("does not interfere with genuine in-dialog scrolling", () => {
    const dialog = document.createElement("div");
    document.body.appendChild(dialog);
    installScrollLock(dialog);
    const innerScroller = document.createElement("div");
    dialog.appendChild(innerScroller);

    expect(dispatchWheel(innerScroller)).toBe(false);
  });

  it("cleans up its listeners", () => {
    const list = buildPortaledList();
    const cleanup = escapeScrollLock(list);
    expect(cleanup).toBeTypeOf("function");

    cleanup?.();
    expect(dispatchWheel(list)).toBe(true);
  });

  it("is a no-op for a null node", () => {
    expect(escapeScrollLock(null)).toBeUndefined();
  });
});
