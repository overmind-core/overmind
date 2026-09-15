import { describe, expect, it } from "vitest";

import {
  datasetsSearchSchema,
  evaluationsSearchSchema,
  onboardingSearchSchema,
  optimiserSearchSchema,
  tracesSearchSchema,
  trainingSearchSchema,
} from "./schemas";

describe("schemas", () => {
  describe("tracesSearchSchema", () => {
    it("parses empty object with defaults", () => {
      const result = tracesSearchSchema.parse({});
      expect(result.timeRange).toBe("all");
      expect(result.ordering).toBe("-start_time_ns");
      expect(result.page).toBe(1);
      expect(result.page_size).toBe(25);
      expect(result.view).toBe("executions");
    });

    it("parses valid search params", () => {
      const result = tracesSearchSchema.parse({
        projectId: "proj-1",
        search: "test",
        timeRange: "past24h",
      });
      expect(result.projectId).toBe("proj-1");
      expect(result.timeRange).toBe("past24h");
      expect(result.search).toBe("test");
    });

    it("parses DRF-style filter lookups", () => {
      const result = tracesSearchSchema.parse({
        capability: "abc-123",
        ordering: "-score",
        received_at__gte: "2026-01-01T00:00:00Z",
        score__gte: "0.8",
        total_cost__lte: "0.5",
      });
      expect(result.capability).toBe("abc-123");
      expect(result.score__gte).toBe("0.8");
      expect(result.total_cost__lte).toBe("0.5");
      expect(result.received_at__gte).toBe("2026-01-01T00:00:00Z");
      expect(result.ordering).toBe("-score");
    });

    it("does not keep behaviour or binding_source on the traces search", () => {
      const result = tracesSearchSchema.parse({
        behaviour: "b1",
        binding_source: "unbound",
        projectId: "proj-1",
      });
      expect(result.projectId).toBe("proj-1");
      expect(result).not.toHaveProperty("behaviour");
      expect(result).not.toHaveProperty("binding_source");
    });

    it("keeps the span-level filter params the quick chips and builder emit", () => {
      // Zod strips undeclared keys, dropping the filter between URL and request.
      const result = tracesSearchSchema.parse({
        has_model: "true",
        min_duration_ms: "5000",
        operation__icontains: "chat",
        service_name__icontains: "worker",
        span_type: "llm_call",
        status_code: "1",
      });
      expect(result.has_model).toBe("true");
      expect(result.span_type).toBe("llm_call");
      expect(result.status_code).toBe("1");
      expect(result.min_duration_ms).toBe("5000");
      expect(result.service_name__icontains).toBe("worker");
      expect(result.operation__icontains).toBe("chat");
    });
  });

  describe("evaluationsSearchSchema", () => {
    it("parses empty with unfiltered defaults", () => {
      const result = evaluationsSearchSchema.parse({});
      expect(result.view).toBe("runs");
      expect(result.run_status).toBe("all");
      expect(result.run_capability).toBe("all");
      expect(result.run_dataset).toBe("all");
      expect(result.run_search).toBe("");
    });
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
    it("keeps the library's capability preset separate from the runs capability filter", () => {
      const result = evaluationsSearchSchema.parse({ capability: "capability-1", view: "library" });
      expect(result.capability).toBe("capability-1");
      expect(result.run_capability).toBe("all");
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
    it("defaults to unfiltered", () => {
      const result = optimiserSearchSchema.parse({});
      expect(result.run_status).toBe("all");
      expect(result.run_capability).toBe("all");
      expect(result.run_search).toBe("");
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
    it("parses empty with unfiltered defaults", () => {
      const result = trainingSearchSchema.parse({});
      expect(result.page).toBe(1);
      expect(result.page_size).toBe(25);
      expect(result.ft_status).toBe("all");
      expect(result.ft_dataset).toBe("all");
      expect(result.ft_model).toBe("all");
      expect(result.ft_search).toBe("");
    });
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
    it("parses empty with unfiltered defaults", () => {
      const result = datasetsSearchSchema.parse({});
      expect(result.ds_search).toBe("");
      expect(result.ds_intent).toBe("all");
      expect(result.ds_capability).toBe("all");
    });
    it("accepts the create deep-link param", () => {
      const result = datasetsSearchSchema.parse({ create: true, ds_capability: "capability-1" });
      expect(result.create).toBe(true);
      expect(result.ds_capability).toBe("capability-1");
    });
    it("round-trips every filter a shared datasets link carries", () => {
      const result = datasetsSearchSchema.parse({
        ds_capability: "capability-1",
        ds_intent: "train",
        ds_search: "golden",
        projectId: "proj-1",
      });
      expect(result.ds_capability).toBe("capability-1");
      expect(result.ds_intent).toBe("train");
      expect(result.ds_search).toBe("golden");
      expect(result.projectId).toBe("proj-1");
    });
    it("falls back to unfiltered for stale values, keeping the rest of the search", () => {
      const result = datasetsSearchSchema.parse({
        ds_intent: "ready_with_warnings",
        ds_search: "kept",
        projectId: "proj-1",
      });
      expect(result.ds_intent).toBe("all");
      expect(result.ds_search).toBe("kept");
      expect(result.projectId).toBe("proj-1");
    });
  });
  describe("onboardingSearchSchema", () => {
    it("parses an optional message", () => {
      expect(onboardingSearchSchema.parse({ message: "ok" }).message).toBe("ok");
    });
    it("allows empty search", () => {
      expect(onboardingSearchSchema.parse({}).message).toBeUndefined();
    });
  });
});
