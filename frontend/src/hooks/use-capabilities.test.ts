import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/client", () => ({
  default: {
    capabilities: {
      capabilitiesList: vi.fn(),
    },
  },
}));

import apiClient from "@/client";
import { fetchAllCapabilities } from "./use-capabilities";

const list = apiClient.capabilities.capabilitiesList as ReturnType<typeof vi.fn>;

describe("fetchAllCapabilities", () => {
  beforeEach(() => {
    list.mockReset();
  });

  it("returns a single page when next is null", async () => {
    list.mockResolvedValueOnce({
      count: 2,
      next: null,
      previous: null,
      results: [{ id: "a" }, { id: "b" }],
    });

    const data = await fetchAllCapabilities("proj-1");

    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledWith({
      page: 1,
      pageSize: 100,
      project: "proj-1",
    });
    expect(data.results.map((r) => r.id)).toEqual(["a", "b"]);
    expect(data.count).toBe(2);
  });

  it("walks every DRF page until results cover count", async () => {
    list
      .mockResolvedValueOnce({
        count: 130,
        next: "http://example/api/capabilities/?page=2",
        previous: null,
        results: Array.from({ length: 100 }, (_, i) => ({ id: `p1-${i}` })),
      })
      .mockResolvedValueOnce({
        count: 130,
        next: null,
        previous: "http://example/api/capabilities/?page=1",
        results: Array.from({ length: 30 }, (_, i) => ({ id: `p2-${i}` })),
      });

    const data = await fetchAllCapabilities("proj-1");

    expect(list).toHaveBeenCalledTimes(2);
    expect(list).toHaveBeenNthCalledWith(2, {
      page: 2,
      pageSize: 100,
      project: "proj-1",
    });
    expect(data.results).toHaveLength(130);
    expect(data.next).toBeNull();
  });
});
