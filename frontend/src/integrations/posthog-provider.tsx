import type { ReactNode } from "react";

import { PostHogProvider as BasePostHogProvider } from "@posthog/react";
import posthog from "posthog-js";

if (
  typeof window !== "undefined" &&
  window.location.hostname !== "localhost" &&
  import.meta.env.VITE_PUBLIC_POSTHOG_KEY
) {
  posthog.init(import.meta.env.VITE_PUBLIC_POSTHOG_KEY, {
    api_host: import.meta.env.VITE_PUBLIC_POSTHOG_HOST || "https://us.i.posthog.com",
    capture_pageview: false,
    defaults: "2025-11-30",
    person_profiles: "identified_only",
  });
}

interface PostHogProviderProps {
  children: ReactNode;
}

export default function PostHogProvider({ children }: PostHogProviderProps) {
  if (window.location.hostname === "localhost") {
    return children;
  }
  return <BasePostHogProvider client={posthog}>{children}</BasePostHogProvider>;
}
