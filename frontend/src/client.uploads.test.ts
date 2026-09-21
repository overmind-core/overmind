// @vitest-environment jsdom

import { afterEach, expect, it, vi } from "vitest";

import apiClient from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("inspects uploads through the registered generated client", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ bytes: 120, filename: "rows.csv", rows: 15 }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    })
  );
  vi.stubGlobal("fetch", fetchMock);
  localStorage.setItem("jwt_access", "test-access-token");

  const inspected = await apiClient.uploads.uploadsInspectCreate({
    id: "test-upload",
    inspectUploadRequest: { size: 120 },
  });

  expect(inspected).toEqual({ bytes: 120, filename: "rows.csv", rows: 15 });
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringMatching(/\/api\/uploads\/test-upload\/inspect\/$/),
    expect.objectContaining({
      body: JSON.stringify({ size: 120 }),
      headers: expect.objectContaining({ Authorization: "Bearer test-access-token" }),
      method: "POST",
    })
  );
});
