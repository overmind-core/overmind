// @vitest-environment jsdom
// covers: boot
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@clerk/clerk-react", () => ({
  useAuth: () => ({
    getToken: async () => null,
    isLoaded: true,
    isSignedIn: false,
    signOut: async () => {},
  }),
  useUser: () => ({ user: null }),
}));

const areas = ["localStorage", "sessionStorage"] as const;
const saved = new Map<string, PropertyDescriptor | undefined>();

// Chrome throws on the property read itself when the user blocks site data.
beforeEach(() => {
  for (const area of areas) {
    saved.set(area, Object.getOwnPropertyDescriptor(window, area));
    Object.defineProperty(window, area, {
      configurable: true,
      get() {
        throw new DOMException("Access is denied for this document.", "SecurityError");
      },
    });
  }
});

afterEach(() => {
  cleanup();
  for (const area of areas) {
    const descriptor = saved.get(area);
    if (descriptor) Object.defineProperty(window, area, descriptor);
    else Reflect.deleteProperty(window, area);
  }
  vi.unstubAllEnvs();
  vi.resetModules();
});

it.each([
  ["hosted", "false", "pk_test_blocked"],
  ["self-hosted", "true", ""],
])("the %s Console renders when the browser blocks site storage", async (_, selfHosted, key) => {
  vi.stubEnv("VITE_SELF_HOSTED", selfHosted);
  vi.stubEnv("VITE_CLERK_PUBLISHABLE_KEY", key);
  vi.resetModules();
  const { AuthProvider } = await import("@/contexts/auth-context");

  render(
    <AuthProvider>
      <p>Console</p>
    </AuthProvider>
  );

  expect(screen.getByText("Console")).toBeTruthy();
});
