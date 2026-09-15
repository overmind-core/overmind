import { describe, expect, it } from "vitest";

import { formatPayload, renderPayload, toFormatted } from "./payload-format";

describe("toFormatted", () => {
  it("renders JSON objects as key: value lines, distinct from the raw payload", () => {
    const pretty = '{\n  "isInvoice": true,\n  "vendor": "Spotify",\n  "amount": 240.5\n}';
    const formatted = toFormatted(pretty);
    expect(formatted).toBe("isInvoice: true\nvendor: Spotify\namount: 240.5");
    expect(formatted).not.toBe(pretty);
  });

  it("formats compact JSON the same as pretty JSON", () => {
    expect(toFormatted('{"a":1,"b":null}')).toBe("a: 1\nb: null");
  });

  it("indents nested objects and arrays", () => {
    expect(toFormatted('{"outer":{"inner":"x"},"list":["a","b"]}')).toBe(
      "outer:\n  inner: x\nlist:\n  0: a\n  1: b"
    );
  });

  it("drops multiline string values onto their own indented block", () => {
    expect(toFormatted('{"summary":"line one\\nline two"}')).toBe(
      "summary:\n  line one\n  line two"
    );
  });

  it("passes non-JSON text and JSON scalars through untouched", () => {
    expect(toFormatted("plain prose, not JSON")).toBe("plain prose, not JSON");
    expect(toFormatted("42")).toBe("42");
    expect(toFormatted("")).toBe("");
  });
});

describe("renderPayload", () => {
  it("returns the literal payload in raw mode", () => {
    const raw = '{"a": 1}';
    expect(renderPayload(raw, "raw")).toBe(raw);
    expect(renderPayload(raw, "formatted")).toBe("a: 1");
  });
});

describe("formatPayload", () => {
  it("renders objects as key: value lines and omits empty payloads", () => {
    expect(formatPayload({ kind: "trace", limit: 50, query: "binder" })).toBe(
      "kind: trace\nlimit: 50\nquery: binder"
    );
    expect(formatPayload("{}")).toBe("");
    expect(formatPayload([])).toBe("");
    expect(formatPayload(undefined)).toBe("");
  });

  it("summarizes a results array to a count plus useful fields", () => {
    const results = Array.from({ length: 5 }, (_, i) => ({
      extra: "omit",
      id: `n${i}`,
      status: "ok",
      summary: `hit ${i}`,
    }));
    expect(formatPayload({ results })).toBe(
      [
        "results:",
        "  count: 5",
        "  items:",
        "    0:",
        "      id: n0",
        "      status: ok",
        "      summary: hit 0",
        "    1:",
        "      id: n1",
        "      status: ok",
        "      summary: hit 1",
        "    2:",
        "      id: n2",
        "      status: ok",
        "      summary: hit 2",
      ].join("\n")
    );
  });
});
