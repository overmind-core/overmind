// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authLocalCreate: vi.fn(),
  invalidateQueries: vi.fn(),
  setTokens: vi.fn(),
}));

vi.mock("@/client", () => ({
  default: { auth: { authLocalCreate: mocks.authLocalCreate } },
  setTokens: mocks.setTokens,
}));

vi.mock("@/integrations/tanstack-query", () => ({
  getContext: () => ({ queryClient: { invalidateQueries: mocks.invalidateQueries } }),
}));

import { LocalLoginForm } from "./components/local-login-form";

describe("LocalLoginForm", () => {
  beforeEach(() => {
    mocks.authLocalCreate.mockReset();
    mocks.setTokens.mockReset();
    mocks.invalidateQueries.mockReset();
  });

  it("posts email and password to authLocalCreate and stores tokens", async () => {
    mocks.authLocalCreate.mockResolvedValue({
      access: "access-token",
      refresh: "refresh-token",
      user: { email: "ops@example.com" },
    });
    const onSignedIn = vi.fn();

    render(<LocalLoginForm onSignedIn={onSignedIn} />);

    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "ops@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => {
      expect(mocks.authLocalCreate).toHaveBeenCalledWith({
        localSessionRequest: { email: "ops@example.com", password: "password123" },
      });
    });
    expect(mocks.setTokens).toHaveBeenCalledWith("access-token", "refresh-token");
    expect(onSignedIn).toHaveBeenCalled();
  });
});
