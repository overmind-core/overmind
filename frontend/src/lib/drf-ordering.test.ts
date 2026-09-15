import { describe, expect, it } from "vitest";

import { cycleDrfOrdering, parseDrfOrdering } from "./drf-ordering";

describe("cycleDrfOrdering", () => {
  it("cycles unset → asc → desc → unset", () => {
    expect(cycleDrfOrdering(undefined, "name")).toBe("name");
    expect(cycleDrfOrdering("name", "name")).toBe("-name");
    expect(cycleDrfOrdering("-name", "name")).toBeUndefined();
  });

  it("starts fresh when switching fields", () => {
    expect(cycleDrfOrdering("-created_at", "name")).toBe("name");
    expect(cycleDrfOrdering("status", "name")).toBe("name");
  });
});

describe("parseDrfOrdering", () => {
  it("parses asc, desc, and empty", () => {
    expect(parseDrfOrdering(undefined)).toBeNull();
    expect(parseDrfOrdering("")).toBeNull();
    expect(parseDrfOrdering("name")).toEqual({ dir: "asc", field: "name" });
    expect(parseDrfOrdering("-created_at")).toEqual({ dir: "desc", field: "created_at" });
  });
});
