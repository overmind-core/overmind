import posthog from "posthog-js";

const selfHosted = import.meta.env.VITE_SELF_HOSTED === "true";

if (!selfHosted && import.meta.env.MODE !== "development") {
  posthog.init("phc_XrIVhixaz5sOqrdzpRwwqlvKXilmcy3PWPgdk0pemZa", {
    api_host: "https://v.overmindlab.ai",
    defaults: "2026-01-30",
    person_profiles: "identified_only",
  });
}

if (!selfHosted && import.meta.env.MODE !== "development" && typeof window !== "undefined") {
  window.addEventListener("error", (event) => {
    posthog.captureException(event.error ?? new Error(String(event.message)));
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason instanceof Error ? event.reason : new Error(String(event.reason));
    posthog.captureException(reason);
  });
}

export const trackEvent = (event: string, properties?: Record<string, any>) => {
  if (!selfHosted && import.meta.env.MODE !== "development") {
    posthog.capture(event, properties);
  }
};

export const identifyUser = ({
  userId,
  clerkUserId,
}: Partial<{ userId: string; clerkUserId: string }>) => {
  if (!selfHosted && import.meta.env.MODE !== "development" && userId && clerkUserId) {
    posthog.identify(userId, { clerkUserId: clerkUserId });
  }
};
