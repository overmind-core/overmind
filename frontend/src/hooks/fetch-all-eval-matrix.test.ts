import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/client", () => ({
  default: { evalMatrix: { evalMatrixList: vi.fn() } },
}));

import apiClient from "@/client";
import { fetchAllEvalMatrix } from "./use-evaluations";

const list = vi.mocked(apiClient.evalMatrix.evalMatrixList);

describe("fetchAllEvalMatrix", () => {
  beforeEach(() => list.mockReset());

  it("retains tool coverage after the API's 100-row page boundary", async () => {
    const rows = Array.from({ length: 190 }, (_, index) => ({
      capability: index % 2 ? "personal-assistant" : null,
      createdAt: new Date("2026-10-13T00:00:00Z"),
      definitionSource: "custom" as const,
      id: String(index),
      name: `Tool ${index}`,
      project: "hermes",
      source: "The result matches the requested operation.",
      spanName: `tool_${index}`,
      type: "llm" as const,
      updatedAt: new Date("2026-10-13T00:00:00Z"),
    }));
    list
      .mockResolvedValueOnce({
        count: 190,
        next: "?page=2",
        previous: null,
        results: rows.slice(0, 100),
      })
      .mockResolvedValueOnce({
        count: 190,
        next: null,
        previous: "?page=1",
        results: rows.slice(100),
      });

    const data = await fetchAllEvalMatrix("hermes");

    expect(data.results).toEqual(rows);
    expect(data.next).toBeNull();
    expect(data.previous).toBeNull();
    expect(list).toHaveBeenCalledTimes(2);
    expect(list).toHaveBeenNthCalledWith(2, {
      ordering: "name",
      page: 2,
      pageSize: 100,
      project: "hermes",
    });
  });

  it("rejects a failed later page instead of presenting partial coverage", async () => {
    list
      .mockResolvedValueOnce({ count: 190, next: "?page=2", previous: null, results: [] })
      .mockRejectedValueOnce(new Error("API unavailable"));

    await expect(fetchAllEvalMatrix("hermes")).rejects.toThrow("API unavailable");
  });
});
