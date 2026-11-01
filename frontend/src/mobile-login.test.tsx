// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://localhost:5173"}

import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@clerk/clerk-react", () => ({
  SignIn: (props: Record<string, string>) => (
    <div data-flow={props.oauthFlow} data-next={props.forceRedirectUrl} data-testid="clerk" />
  ),
  SignUp: (props: Record<string, string>) => (
    <div data-flow={props.oauthFlow} data-next={props.forceRedirectUrl} data-testid="clerk" />
  ),
}));
vi.mock("@/config", () => ({ config: { clerkReady: true } }));
vi.mock("@/contexts/auth-context", () => ({
  useAuthContext: () => ({
    isGuest: false,
    isLoaded: true,
    isSignedIn: false,
    refreshAuth: () => {},
  }),
}));
vi.mock("@/components/splash-background", () => ({
  SplashBackground: ({ children }: { children: React.ReactNode }) => children,
}));

import { Route as LoginRoute } from "@/routes/login";

const root = createRootRoute();
const login = LoginRoute.update({
  getParentRoute: () => root,
  id: "/login",
  path: "/login",
} as never);
const routeTree = root.addChildren([login]);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(window.navigator, "maxTouchPoints");
  window.history.replaceState(null, "", "/");
});

describe.each(["signin", "signup"])("%s OAuth handoff", (mode) => {
  it.each([
    ["iPhone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)", 5, "redirect"],
    ["Android", "Mozilla/5.0 (Linux; Android 15) Chrome/130.0 Mobile", 5, "redirect"],
    [
      "iPad desktop mode",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.0 Safari/605.1.15",
      5,
      "redirect",
    ],
    [
      "desktop Mac",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/18.0 Safari/605.1.15",
      0,
      "popup",
    ],
    ["touchscreen Windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0", 10, "popup"],
  ])("uses the intended browser flow on %s and preserves MCP consent", async (_, userAgent, maxTouchPoints, flow) => {
    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(userAgent as string);
    Object.defineProperty(window.navigator, "maxTouchPoints", {
      configurable: true,
      value: maxTouchPoints,
    });
    const next = "/oauth/authorize?request=abc_123-xyz";
    const url = `/login?next=${encodeURIComponent(next)}${mode === "signup" ? "&mode=signup" : ""}`;
    window.history.replaceState(null, "", url);
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: [url] }),
      routeTree,
    });
    render(<RouterProvider router={router} />);
    const clerk = await screen.findByTestId("clerk");
    expect(clerk.getAttribute("data-flow")).toBe(flow);
    expect(clerk.getAttribute("data-next")).toBe(next);
  });
});
