const apiUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const clerkPk = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY ?? "";

const overmindPypiVersion = import.meta.env.VITE_LATEST_CLI_VERSION ?? "";

const sdkEditablePath = import.meta.env.VITE_SDK_EDITABLE_PATH ?? "";

const sdkGitRef = import.meta.env.VITE_SDK_GIT_REF ?? "main";

export const config = {
  apiUrl,
  clerkPk,
  clerkReady: true,
  overmindPypiVersion,
  sdkEditablePath,
  sdkGitRef,
};
