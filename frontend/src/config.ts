const clerkPublishableKey: string | null = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

export const config = {
  apiUrl: import.meta.env.VITE_API_URL,
  clerkPublishableKey,
  isSelfHosted: !clerkPublishableKey,
};
