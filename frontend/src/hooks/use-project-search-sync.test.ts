import { describe, expect, it } from "vitest";

import { projectIdFromProjectsDetailPath } from "./use-project-search-sync";

describe("projectIdFromProjectsDetailPath", () => {
  it("returns the id on a project settings path", () => {
    expect(projectIdFromProjectsDetailPath("/projects/abc")).toBe("abc");
    expect(projectIdFromProjectsDetailPath("/projects/abc/")).toBe("abc");
  });

  it("returns undefined on the projects list", () => {
    expect(projectIdFromProjectsDetailPath("/projects")).toBeUndefined();
    expect(projectIdFromProjectsDetailPath("/projects/")).toBeUndefined();
  });

  it("returns undefined on nested project paths", () => {
    expect(projectIdFromProjectsDetailPath("/projects/abc/members")).toBeUndefined();
  });
});
