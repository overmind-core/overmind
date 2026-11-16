// covers: live-console
import { describe, expect, it } from "vitest";

import {
  datasetsSearchSchema,
  evaluationsSearchSchema,
  optimiserSearchSchema,
  trainingSearchSchema,
} from "./schemas";

describe("schemas", () => {
  describe("evaluationsSearchSchema", () => {
    it("round-trips a deep link carrying both a page and runs filters", () => {
      const result = evaluationsSearchSchema.parse({
        page: "3",
        run_capability: "capability-1",
        run_dataset: "ds-1",
        run_search: "nightly",
        run_status: "failed",
      });
      expect(result.page).toBe(3);
      expect(result.run_capability).toBe("capability-1");
      expect(result.run_dataset).toBe("ds-1");
      expect(result.run_search).toBe("nightly");
      expect(result.run_status).toBe("failed");
    });
    // `run_status` is forwarded to `?status=`, so a stale value must neither
    // survive nor throw — throwing discards every other search param with it.
    it("falls back to unfiltered for a run_status outside the closed set", () => {
      const result = evaluationsSearchSchema.parse({ page: "2", run_status: "foo" });
      expect(result.run_status).toBe("all");
      expect(result.page).toBe(2);
    });
    // The eval-runs endpoint 400s on `partially_completed`, a status only the
    // generic Job model carries.
    it("falls back to unfiltered for a status only the generic Job model has", () => {
      const result = evaluationsSearchSchema.parse({ run_status: "partially_completed" });
      expect(result.run_status).toBe("all");
    });
  });

  describe("optimiserSearchSchema", () => {
    it("round-trips a deep link carrying both a page and filters", () => {
      const result = optimiserSearchSchema.parse({
        page: "2",
        run_capability: "capability-1",
        run_search: "gpt",
        run_status: "running",
      });
      expect(result.page).toBe(2);
      expect(result.run_capability).toBe("capability-1");
      expect(result.run_search).toBe("gpt");
      expect(result.run_status).toBe("running");
    });
    it("accepts optimize + capability deep-link params", () => {
      const result = optimiserSearchSchema.parse({
        capabilityId: "capability-1",
        optimize: true,
      });
      expect(result.optimize).toBe(true);
      expect(result.capabilityId).toBe("capability-1");
    });
  });

  describe("trainingSearchSchema", () => {
    it("round-trips a page, the history filters and an open run together", () => {
      const result = trainingSearchSchema.parse({
        ft_dataset: "ds-1",
        ft_model: "meta/llama-3.1-8b",
        ft_search: "nightly",
        ft_status: "failed",
        groupId: "grp-1",
        jobId: "job-1",
        page: "3",
        page_size: "50",
      });
      expect(result.page).toBe(3);
      expect(result.page_size).toBe(50);
      expect(result.ft_dataset).toBe("ds-1");
      expect(result.ft_model).toBe("meta/llama-3.1-8b");
      expect(result.ft_search).toBe("nightly");
      expect(result.ft_status).toBe("failed");
      expect(result.groupId).toBe("grp-1");
      expect(result.jobId).toBe("job-1");
    });
    // `ft_status` is forwarded to `?status=`, so a stale value must neither
    // survive nor throw — throwing would take the open run with it.
    it("falls back to unfiltered for an ft_status outside the closed set", () => {
      const result = trainingSearchSchema.parse({ ft_status: "kaput", groupId: "grp-1" });
      expect(result.ft_status).toBe("all");
      expect(result.groupId).toBe("grp-1");
    });
  });

  describe("datasetsSearchSchema", () => {
    it("accepts the create deep-link param", () => {
      const result = datasetsSearchSchema.parse({ create: true, ds_capability: "capability-1" });
      expect(result.create).toBe(true);
      expect(result.ds_capability).toBe("capability-1");
    });
    it("round-trips every filter a shared datasets link carries", () => {
      const result = datasetsSearchSchema.parse({
        ds_capability: "capability-1",
        ds_role: "training",
        ds_search: "golden",
        projectId: "proj-1",
      });
      expect(result.ds_capability).toBe("capability-1");
      expect(result.ds_role).toBe("training");
      expect(result.ds_search).toBe("golden");
      expect(result.projectId).toBe("proj-1");
    });
    it("falls back to unfiltered for stale values, keeping the rest of the search", () => {
      const result = datasetsSearchSchema.parse({
        ds_role: "ready_with_warnings",
        ds_search: "kept",
        projectId: "proj-1",
      });
      expect(result.ds_role).toBe("all");
      expect(result.ds_search).toBe("kept");
      expect(result.projectId).toBe("proj-1");
    });
  });
});
