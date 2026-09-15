import { describe, expect, it } from "vitest";

import { tracesSearchSchema } from "@/lib/schemas";
import { buildTracesListRequest } from "./use-traces";

const base = { page: 1, projectId: "proj-1" };

describe("buildTracesListRequest", () => {
  it("sends model, has_model, and the default-lookup service/operation values", () => {
    const request = buildTracesListRequest({
      ...base,
      filters: {
        // A string in the URL, a boolean on the wire.
        has_model: "true",
        model: "ft-model-x",
        // The UI's default lookup is `icontains`; the backend has only the bare
        // param, which already matches case-insensitively.
        operation__icontains: "chat",
        service_name__icontains: "worker",
      },
    });

    expect(request).toMatchObject({
      hasModel: true,
      model: "ft-model-x",
      operation: "chat",
      serviceName: "worker",
    });
  });

  // Zod strips undeclared keys, so a param the schema forgets to declare never
  // reaches the request whatever the builder does with it.
  it("sends the span-level params a URL carries, through the search schema", () => {
    const search = tracesSearchSchema.parse({
      max_duration_ms: "9000",
      min_duration_ms: "5000",
      span_type: "llm_call",
      status_code: "2",
    });
    // The view hands the parsed search's string params on as the filter map.
    const filters = Object.fromEntries(
      Object.entries(search).filter((e): e is [string, string] => typeof e[1] === "string")
    );

    const request = buildTracesListRequest({ ...base, filters });

    expect(request).toMatchObject({
      maxDurationMs: 9000,
      minDurationMs: 5000,
      spanType: "llm_call",
      statusCode: 2,
    });
  });

  // OTel UNSET: the guard tests the URL string, not the number, so a real
  // filter value must not be dropped for looking falsy.
  it("keeps status_code=0", () => {
    const request = buildTracesListRequest({ ...base, filters: { status_code: "0" } });

    expect(request.statusCode).toBe(0);
  });

  it("omits absent filters rather than sending empty values", () => {
    const request = buildTracesListRequest({ ...base, filters: {} });

    expect(request.model).toBeUndefined();
    expect(request.hasModel).toBeUndefined();
    expect(request.serviceName).toBeUndefined();
  });
});
