// A deep-linked page has to survive the route's own search validation: zod
// strips keys the schema doesn't declare, and the request follows the parsed
// search, not the URL.
// `-` prefix: not a route, per `routeFileIgnorePrefix`.
import { describe, expect, it, vi } from "vitest";

import { projectIdSearchSchema } from "@/lib/schemas";

// Under vitest the router plugin is off, so importing the route pulls its whole
// component graph rather than the split chunk — and the @lobehub icon ESM
// subpaths behind the provider logo don't resolve in that resolver.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  ModelProviderChip: () => null,
}));

import { Route } from "./inference";
import { Route as DetailRoute } from "./inference_.$modelId";

type Search = {
  inf_capability?: string;
  inf_search?: string;
  page?: number;
  page_size?: number;
  projectId?: string;
};

const parseWith = (route: unknown, raw: Record<string, unknown>) =>
  (
    route as { options: { validateSearch: { parse: (v: unknown) => unknown } } }
  ).options.validateSearch.parse(raw) as Search;

const parse = (raw: Record<string, unknown>) => parseWith(Route, raw);
const parseDetail = (raw: Record<string, unknown>) => parseWith(DetailRoute, raw);

describe("Inference route search", () => {
  it("keeps a deep-linked page and page size, coerced from the URL's strings", () => {
    expect(parse({ page: "3", page_size: "10", projectId: "proj-1" })).toEqual({
      inf_capability: "all",
      inf_search: "",
      page: 3,
      page_size: 10,
      projectId: "proj-1",
    });
  });

  // The filters scope the request, so a missing one must parse to "unfiltered"
  // rather than let `undefined` reach the query.
  it("falls back to the unfiltered first page", () => {
    expect(parse({ projectId: "proj-1" })).toEqual({
      inf_capability: "all",
      inf_search: "",
      page: 1,
      page_size: 25,
      projectId: "proj-1",
    });
  });
});

// The model URL is the only copy of the list's filters, and every affordance
// back out rebuilds the list URL from it.
describe("Model detail route search", () => {
  it("carries the list's filters and page through the detail route", () => {
    const deepLink = {
      inf_capability: "capability-1",
      inf_search: "llama",
      page: "3",
      page_size: "10",
      projectId: "proj-1",
    };

    expect(parseDetail(deepLink)).toEqual({
      inf_capability: "capability-1",
      inf_search: "llama",
      page: 3,
      page_size: 10,
      projectId: "proj-1",
    });
    // The bare project schema keeps `projectId` alone — widening it is what
    // preserves the rest.
    expect(projectIdSearchSchema.parse(deepLink)).toEqual({ projectId: "proj-1" });
  });

  it("carries the no-capability bucket through too", () => {
    expect(parseDetail({ inf_capability: "__none__", projectId: "proj-1" }).inf_capability).toBe(
      "__none__"
    );
  });

  it("still parses a bare project link into the list's defaults", () => {
    expect(parseDetail({ projectId: "proj-1" })).toMatchObject({
      inf_capability: "all",
      inf_search: "",
      page: 1,
      page_size: 25,
    });
  });
});
