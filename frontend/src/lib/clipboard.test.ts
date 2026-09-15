// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { writeClipboardText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("writeClipboardText", () => {
  it("starts a deferred clipboard write before the text resolves", async () => {
    let resolveText!: (text: string) => void;
    const text = new Promise<string>((resolve) => {
      resolveText = resolve;
    });
    const write = vi.fn().mockResolvedValue(undefined);
    class ClipboardItemMock {
      data: unknown;

      constructor(data: unknown) {
        this.data = data;
      }
    }

    vi.stubGlobal("ClipboardItem", ClipboardItemMock);
    vi.stubGlobal("navigator", { clipboard: { write } });

    const copy = writeClipboardText(text);
    expect(write).toHaveBeenCalledOnce();

    resolveText("onboarding prompt");
    await copy;

    const item = write.mock.calls[0][0][0] as {
      data: { "text/plain": Promise<Blob> };
    };
    expect(await (await item.data["text/plain"]).text()).toBe("onboarding prompt");
  });
});
