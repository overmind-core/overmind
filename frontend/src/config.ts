const apiUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const clerkPk = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY ?? "";

const sdkEditablePath = import.meta.env.VITE_SDK_EDITABLE_PATH ?? "";

// Self-hosted Console: blank publishable key + VITE_SELF_HOSTED=true skips Clerk.
export const config = {
  apiUrl,
  clerkPk,
  clerkReady: Boolean(clerkPk) && import.meta.env.VITE_SELF_HOSTED !== "true",
  sdkEditablePath,
};
