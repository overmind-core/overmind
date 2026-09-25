import { describe, expect, it } from "vitest";

import { domainStatus, scoreChipClass, scoreTone, TONE_CHIP } from "./colors";

describe("scoreTone", () => {
  it("cuts at 70 and 40", () => {
    expect(scoreTone(100)).toBe("success");
    expect(scoreTone(70)).toBe("success");
    expect(scoreTone(69.9)).toBe("warning");
    expect(scoreTone(40)).toBe("warning");
    expect(scoreTone(39.9)).toBe("error");
    expect(scoreTone(0)).toBe("error");
  });

  it("treats a missing score as no signal, not as failure", () => {
    expect(scoreChipClass(null)).toBe(TONE_CHIP.neutral);
    expect(scoreChipClass(undefined)).toBe(TONE_CHIP.neutral);
    expect(scoreChipClass(85)).toBe(TONE_CHIP.success);
  });
});

describe("domainStatus", () => {
  it("agrees on in-flight states across domains", () => {
    expect(domainStatus("running")).toBe("info");
    expect(domainStatus("queued")).toBe("info");
    expect(domainStatus("pending")).toBe("info");
    expect(domainStatus("deploying")).toBe("info");
  });

  it("maps terminal states", () => {
    expect(domainStatus("completed")).toBe("success");
    expect(domainStatus("succeeded")).toBe("success");
    expect(domainStatus("failed")).toBe("error");
    expect(domainStatus("cancelled")).toBe("error");
    expect(domainStatus("partially_completed")).toBe("warning");
  });

  it("is case- and whitespace-insensitive", () => {
    expect(domainStatus("  RUNNING ")).toBe("info");
    expect(domainStatus("Failed")).toBe("error");
  });

  it("falls back to neutral, never to a success colour", () => {
    expect(domainStatus("something_new_from_the_api")).toBe("neutral");
    expect(domainStatus("")).toBe("neutral");
    expect(domainStatus(null)).toBe("neutral");
    expect(domainStatus(undefined)).toBe("neutral");
  });
});

describe("tone maps", () => {
  it("never contains a raw colour value", () => {
    for (const cls of Object.values(TONE_CHIP)) {
      expect(cls).not.toMatch(/#[0-9a-f]{3,6}|hsl\(|rgb\(/i);
    }
  });

  it("never uses a dark: variant (status tokens are already per-theme)", () => {
    for (const cls of Object.values(TONE_CHIP)) expect(cls).not.toContain("dark:");
  });

  // `border-border` lands at ~1.25:1 against a chip's own fill — the edge
  // disappears and the chip reads as bare text.
  it("tints every chip border from its tone's ink, never from the divider token", () => {
    for (const [tone, cls] of Object.entries(TONE_CHIP)) {
      const border = cls.split(" ").find((c) => c.startsWith("border-"));
      expect(border, `${tone} has no border class`).toBeDefined();
      expect(border, `${tone} uses the divider token`).not.toBe("border-border");
      expect(border, `${tone} border is not alpha-tinted`).toMatch(/^border-[a-z-]+\/\d+$/);
    }
  });
});
