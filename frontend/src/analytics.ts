const selfHosted = import.meta.env.VITE_SELF_HOSTED === "true";
const enabled = !selfHosted && import.meta.env.MODE !== "development";

const client = enabled
  ? import("posthog-js")
      .then(({ default: posthog }) => {
        posthog.init(
          import.meta.env.VITE_PUBLIC_POSTHOG_KEY ||
            "phc_XrIVhixaz5sOqrdzpRwwqlvKXilmcy3PWPgdk0pemZa",
          {
            api_host: import.meta.env.VITE_PUBLIC_POSTHOG_HOST || "https://v.overmindlab.ai",
            defaults: "2026-01-30",
            person_profiles: "identified_only",
          }
        );
        return posthog;
      })
      // Analytics delivery must not block rendering or recursively report its own load failure.
      .catch(() => undefined)
  : undefined;

if (enabled && typeof window !== "undefined") {
  window.addEventListener("error", (event) => {
    void client?.then((posthog) =>
      posthog?.captureException(event.error ?? new Error(String(event.message)))
    );
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason instanceof Error ? event.reason : new Error(String(event.reason));
    void client?.then((posthog) => posthog?.captureException(reason));
  });
}

export const trackEvent = (event: string, properties?: Record<string, any>) => {
  void client?.then((posthog) => posthog?.capture(event, properties));
};

export const identifyUser = ({
  userId,
  clerkUserId,
}: Partial<{ userId: string; clerkUserId: string }>) => {
  if (userId && clerkUserId) {
    void client?.then((posthog) => posthog?.identify(userId, { clerkUserId: clerkUserId }));
  }
};
