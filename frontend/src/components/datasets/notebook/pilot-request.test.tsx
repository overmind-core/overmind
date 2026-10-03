// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { PilotRequest } from "./pilot-request";

it("requests an explored, source-bound proposal without changing the active cell", () => {
  const request = vi.fn();
  render(
    <PilotRequest
      cell={{ fingerprint: "source-hash", id: "source-cell", rows: 1000, version: "1.0" }}
      disabled={false}
      onRequest={request}
    />
  );
  fireEvent.click(screen.getByText("Create representative pilot"));
  fireEvent.change(screen.getByLabelText("Pilot rows"), { target: { value: "100" } });
  fireEvent.click(screen.getByText("Prepare pilot proposal"));
  expect(request).toHaveBeenCalledOnce();
  expect(request.mock.calls[0][0]).toContain("source-cell");
  expect(request.mock.calls[0][0]).toContain("source-hash");
  expect(request.mock.calls[0][0]).toContain("100 unchanged rows");
  expect(request.mock.calls[0][0]).toContain("before selecting rows");
});
