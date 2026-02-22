import { describe, expect, it } from "vitest";

import {
  chatbotsSearchSchema,
  jobsSearchSchema,
  onboardingSearchSchema,
  tracesSearchSchema,
} from "./schemas";

describe("schemas", () => {
  describe("tracesSearchSchema", () => {
    it("parses empty object with defaults", () => {
      const result = tracesSearchSchema.parse({});
      expect(result.timeRange).toBe("all");
      expect(result.sortBy).toBe("timestamp");
      expect(result.sortDirection).toBe("desc");
    });
    it("parses valid search params", () => {
      const result = tracesSearchSchema.parse({
        projectId: "proj-1",
        q: "test",
        timeRange: "past24h",
      });
      expect(result.projectId).toBe("proj-1");
      expect(result.timeRange).toBe("past24h");
      expect(result.q).toBe("test");
    });
  });

  describe("chatbotsSearchSchema", () => {
    it("parses empty with defaults", () => {
      const result = chatbotsSearchSchema.parse({});
      expect(result.timeRange).toBe("all");
    });
    it("parses projectId and name", () => {
      const result = chatbotsSearchSchema.parse({
        name: "My Chat",
        projectId: "p1",
      });
      expect(result.projectId).toBe("p1");
      expect(result.name).toBe("My Chat");
    });
  });

  describe("jobsSearchSchema", () => {
    it("parses with defaults", () => {
      const result = jobsSearchSchema.parse({});
      expect(result.job_type).toBe("all");
      expect(result.status).toBe("all");
      expect(result.limit).toBe(100);
    });
    it("parses job_type and status", () => {
      const result = jobsSearchSchema.parse({
        job_type: "template_extraction",
        status: "running",
      });
      expect(result.job_type).toBe("template_extraction");
      expect(result.status).toBe("running");
    });
  });

  describe("onboardingSearchSchema", () => {
    it("parses step 1 and 2", () => {
      expect(onboardingSearchSchema.parse({ step: "1" }).step).toBe("1");
      expect(onboardingSearchSchema.parse({ step: "2" }).step).toBe("2");
    });
    it("allows undefined step", () => {
      expect(onboardingSearchSchema.parse({}).step).toBeUndefined();
    });
  });
});
