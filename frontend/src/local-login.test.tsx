// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://127.0.0.1:5173"}
import { StrictMode } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authLocalCreate: vi.fn(),
  chatgptCallbackSessionCreate: vi.fn(),
  chatgptCallbackSessionRetrieve: vi.fn(),
  chatgptLoginCreate: vi.fn(),
  chatgptLoginRetrieve: vi.fn(),
  invalidateQueries: vi.fn(),
  setTokens: vi.fn(),
}));

vi.mock("@/client", () => ({
  default: {
    auth: { authLocalCreate: mocks.authLocalCreate },
    chatgpt: {
      chatgptCallbackSessionCreate: mocks.chatgptCallbackSessionCreate,
      chatgptCallbackSessionRetrieve: mocks.chatgptCallbackSessionRetrieve,
      chatgptLoginCreate: mocks.chatgptLoginCreate,
      chatgptLoginRetrieve: mocks.chatgptLoginRetrieve,
    },
  },
  setTokens: mocks.setTokens,
}));

vi.mock("@/integrations/tanstack-query", () => ({
  getContext: () => ({ queryClient: { invalidateQueries: mocks.invalidateQueries } }),
}));

import { LocalLoginForm } from "./components/local-login-form";

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.chatgptCallbackSessionCreate.mockReset();
  mocks.chatgptLoginRetrieve.mockResolvedValue({ enabled: true, rememberedEmail: "" });
  mocks.chatgptCallbackSessionRetrieve.mockResolvedValue({
    email: "ops@example.com",
    requiresPassword: false,
  });
  mocks.authLocalCreate.mockReset();
  mocks.setTokens.mockReset();
  mocks.invalidateQueries.mockReset();
});

describe("LocalLoginForm", () => {
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

it("starts ChatGPT without password fields and retains password login after a failure", async () => {
  mocks.chatgptLoginCreate.mockRejectedValue(new Error("Sign-in unavailable"));
  render(<LocalLoginForm onSignedIn={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Continue with ChatGPT" }));
  await waitFor(() => expect(mocks.chatgptLoginCreate).toHaveBeenCalled());
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByLabelText("Password")).toBeTruthy();
  expect(mocks.authLocalCreate).not.toHaveBeenCalled();
});

it("redeems the browser handoff once in StrictMode and establishes a local session", async () => {
  mocks.chatgptCallbackSessionCreate.mockResolvedValue({
    access: "chatgpt-session",
    refresh: "chatgpt-refresh",
  });
  const onSignedIn = vi.fn();
  render(
    <StrictMode>
      <LocalLoginForm chatgptComplete onSignedIn={onSignedIn} />
    </StrictMode>
  );
  await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
  expect(mocks.chatgptCallbackSessionCreate).toHaveBeenCalledTimes(1);
  expect(mocks.setTokens).toHaveBeenCalledWith("chatgpt-session", "chatgpt-refresh");
});

it("keeps ChatGPT hidden when the backend disables local integration", async () => {
  mocks.chatgptLoginRetrieve.mockResolvedValue({ enabled: false, rememberedEmail: "" });
  render(<LocalLoginForm onSignedIn={vi.fn()} />);
  await waitFor(() => expect(mocks.chatgptLoginRetrieve).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Continue with ChatGPT" })).toBeNull();
});

it("links an existing account in place and retries a wrong password without another OAuth flow", async () => {
  mocks.chatgptCallbackSessionRetrieve.mockResolvedValue({
    email: "ops@example.com",
    requiresPassword: true,
  });
  mocks.chatgptCallbackSessionCreate
    .mockRejectedValueOnce(new Error("Incorrect password"))
    .mockResolvedValueOnce({ access: "linked", refresh: "refresh" });
  const onSignedIn = vi.fn();
  render(<LocalLoginForm chatgptComplete onSignedIn={onSignedIn} />);
  await screen.findByRole("heading", { name: "Connect your existing account" });
  expect(mocks.chatgptCallbackSessionCreate).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Overmind password"), {
    target: { value: "wrong-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect and sign in" }));
  await screen.findByRole("alert");
  fireEvent.change(screen.getByLabelText("Overmind password"), {
    target: { value: "correct-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect and sign in" }));
  await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
  expect(mocks.setTokens).toHaveBeenCalledWith("linked", "refresh");
  expect(mocks.authLocalCreate).not.toHaveBeenCalled();
  expect(mocks.chatgptLoginCreate).not.toHaveBeenCalled();
});
