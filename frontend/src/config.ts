const clerkPublishableKey: string | null = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
const isSelfHosted: boolean = import.meta.env.VITE_SELF_HOSTED === 'true';
const clerkReady: boolean = import.meta.env.VITE_CLERK_READY === 'true';

const apiUrl =
  import.meta.env.VITE_API_BASE_URL ?? (isSelfHosted ? "" : "https://api.overmindlab.ai");

export const config = {
  apiUrl,
  clerkPublishableKey,
  isSelfHosted,
  clerkReady,
};
