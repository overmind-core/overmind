import { describe, expect, it } from "vitest";

import {
  evalPreloadRefetchInterval,
  getEvalPreload,
  isActiveEvalPreloadStatus,
  resolveEvalPreloadStatus,
} from "./eval-preload";

describe("getEvalPreload", () => {
  it("parses a snake_case blob from improvementMetadata", () => {
    const preload = getEvalPreload({
      improvementMetadata: {
        capability_card: { task: "classify" },
        eval_preload: {
          counts: { added: 2, generated: 3 },
          started_at: "2026-07-28T12:00:00Z",
          status: "running",
        },
      },
    });

    expect(preload).toEqual({
      counts: { added: 2, generated: 3 },
      startedAt: "2026-07-28T12:00:00Z",
      status: "running",
    });
  });

  it("returns null when blob is missing or invalid", () => {
    expect(getEvalPreload({})).toBeNull();
    expect(getEvalPreload({ improvementMetadata: null })).toBeNull();
    expect(
      getEvalPreload({ improvementMetadata: { eval_preload: { status: "bogus" } } })
    ).toBeNull();
    expect(getEvalPreload({ improvementMetadata: "nope" })).toBeNull();
  });

  it("parses failed status with error and finished_at", () => {
    const preload = getEvalPreload({
      improvementMetadata: {
        eval_preload: {
          error: "tier1 timeout",
          finished_at: "2026-07-28T12:01:00Z",
          status: "failed",
        },
      },
    });

    expect(preload?.status).toBe("failed");
    expect(preload?.error).toBe("tier1 timeout");
    expect(preload?.finishedAt).toBe("2026-07-28T12:01:00Z");
  });
});

describe("resolveEvalPreloadStatus", () => {
  it("prefers explicit blob status", () => {
    expect(
      resolveEvalPreloadStatus(
        { improvementMetadata: { eval_preload: { status: "pending" } } },
        { defaultSetMemberCount: 5 }
      )
    ).toBe("pending");
  });

  it("infers ready when default set already has members", () => {
    expect(resolveEvalPreloadStatus({}, { defaultSetMemberCount: 2 })).toBe("ready");
  });

  it("returns null when unknown and no members", () => {
    expect(resolveEvalPreloadStatus({})).toBeNull();
  });
});

describe("isActiveEvalPreloadStatus", () => {
  it("treats pending and running as active", () => {
    expect(isActiveEvalPreloadStatus("pending")).toBe(true);
    expect(isActiveEvalPreloadStatus("running")).toBe(true);
    expect(isActiveEvalPreloadStatus("ready")).toBe(false);
    expect(isActiveEvalPreloadStatus(null)).toBe(false);
  });
});

describe("evalPreloadRefetchInterval", () => {
  it("polls only while preload is active", () => {
    expect(evalPreloadRefetchInterval("running")).toBe(3_000);
    expect(evalPreloadRefetchInterval("ready")).toBe(false);
    expect(evalPreloadRefetchInterval(null)).toBe(false);
  });
});
