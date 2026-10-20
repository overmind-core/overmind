// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://127.0.0.1:5173"}

import { QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  current: {
    beginGuestSession: () => {},
    getToken: async () => null,
    isGuest: false,
    isLoaded: true,
    isSignedIn: false,
    refreshAuth: () => {},
    requestUpgrade: () => {},
    signOut: async () => {},
    user: null,
  } as Record<string, unknown>,
  listeners: new Set<() => void>(),
}));

vi.mock("@/contexts/auth-context", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useAuthContext: () =>
      useSyncExternalStore(
        (cb) => {
          auth.listeners.add(cb);
          return () => auth.listeners.delete(cb);
        },
        () => auth.current
      ),
  };
});
// The self-hosted form is what these tests drive; local .env files must not flip it to Clerk.
vi.mock("@/config", async (importOriginal) => {
  const { config } = await importOriginal<typeof import("@/config")>();
  return { config: { ...config, clerkReady: false } };
});
vi.mock("@/hooks/use-query", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOnboardingStatus: () => ({ data: undefined, isLoading: false }),
}));
// The shell is out of scope here; only the auth gate and the login route run.
vi.mock("@/components/app-sidebar", () => ({ AppSidebar: () => null }));
vi.mock("@/components/command-palette", () => ({ CommandPalette: () => null }));
vi.mock("@/components/datasets/workshop-sidebar", () => ({ WorkshopSidebar: () => null }));

import { AuthRouter } from "@/components/auth-router";
import { getContext } from "@/integrations/tanstack-query";
import { safeNextPath } from "@/lib/auth-redirect";
import { Route as AuthRouteFile } from "@/routes/_auth";
import { Route as LoginRouteFile } from "@/routes/login";

// The real gate and login route under a stub app: any other path is a protected page.
const root = createRootRoute();
const login = LoginRouteFile.update({
  getParentRoute: () => root,
  id: "/login",
  path: "/login",
} as never);
const gate = AuthRouteFile.update({ getParentRoute: () => root, id: "/_auth" } as never);
const home = createRoute({ component: () => null, getParentRoute: () => gate, path: "/" });
const page = createRoute({ component: () => null, getParentRoute: () => gate, path: "$" });
const routeTree = root.addChildren([login, gate.addChildren([home, page])]);

const replace = vi.fn();

function mount(initial: string) {
  const history = createMemoryHistory({ initialEntries: [initial] });
  const context = getContext();
  const router = createRouter({ context, history, routeTree });
  const view = () => (
    <QueryClientProvider client={context.queryClient}>
      <AuthRouter router={router} />
    </QueryClientProvider>
  );
  render(view());
  // jsdom's location is not spy-able and the memory history never touches it;
  // login reads origin/search/replace from it.
  vi.stubGlobal("location", {
    origin: "http://127.0.0.1:5173",
    replace,
    get search() {
      return history.location.search;
    },
  });
  return { history, router };
}

function setAuth(patch: Record<string, unknown>) {
  auth.current = { ...auth.current, ...patch };
  for (const notify of auth.listeners) notify();
}

beforeEach(() => {
  window.matchMedia ??= ((query: string) => ({
    addEventListener: () => {},
    matches: false,
    media: query,
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
  setAuth({ isGuest: false, isLoaded: true, isSignedIn: false });
  replace.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("auth redirect", () => {
  it("sends a signed-out visitor to login carrying the full destination, without a history entry", async () => {
    const { history, router } = mount("/datasets?x=1#frag");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toMatchObject({ next: "/datasets?x=1#frag" });
    expect(history.length).toBe(1);
  });

  it("does not carry a next for the home page", async () => {
    const { router } = mount("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).not.toHaveProperty("next");
  });

  it("settles on login without redirect loops while signed out", async () => {
    const { router } = mount("/capabilities/abc?projectId=p1");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    const settled = router.state.location.href;
    await new Promise((r) => setTimeout(r, 200));
    expect(router.state.location.href).toBe(settled);
    expect(replace).not.toHaveBeenCalled();
  });

  it("returns to the destination once signed in", async () => {
    const { router } = mount("/capabilities/abc?projectId=p1");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    act(() => setAuth({ isSignedIn: true }));
    await waitFor(() => expect(replace).toHaveBeenCalledTimes(1));
    expect(replace).toHaveBeenCalledWith("/capabilities/abc?projectId=p1");
  });

  it("lands on login when the user signs out of a protected page", async () => {
    setAuth({ isSignedIn: true });
    const { router } = mount("/datasets");
    await waitFor(() => expect(router.state.location.pathname).toBe("/datasets"));
    act(() => setAuth({ isSignedIn: false }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    const settled = router.state.location.href;
    await new Promise((r) => setTimeout(r, 200));
    expect(router.state.location.href).toBe(settled);
    expect(replace).not.toHaveBeenCalled();
  });

  it("goes home when signed in at login without a destination", async () => {
    setAuth({ isSignedIn: true });
    const { router } = mount("/login");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores an off-origin destination", async () => {
    const { router } = mount("/login?next=%2F%5Cevil.com");
    act(() => setAuth({ isSignedIn: true }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("safeNextPath", () => {
  it.each(["/", "/a?b=1#c", "/login"])("keeps %s", (p) => expect(safeNextPath(p)).toBe(p));
  it.each([
    "//evil.com",
    "/\\evil.com",
    "/\t/evil.com",
    "https://evil.com",
    "evil.com",
    "",
    null,
    undefined,
  ])("rejects %j", (p) => expect(safeNextPath(p)).toBeNull());
});
