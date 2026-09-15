import { config } from "@/config";

// API keys are shown once at creation, so the console caches the key in the
// browser that minted it. Keys are scoped by API host — local and production
// keys are not interchangeable. Cached keys are validated on read because
// local DB resets (make clean) and revocations leave stale entries.
const ACCOUNT_STORAGE_PREFIX = "telemetry_api_key_account";

function accountStorageKey(apiUrl: string): string {
  try {
    return `${ACCOUNT_STORAGE_PREFIX}:${new URL(apiUrl).host}`;
  } catch {
    return ACCOUNT_STORAGE_PREFIX;
  }
}

function apiBaseUrl(apiUrl?: string): string {
  return (apiUrl ?? config.apiUrl).replace(/\/$/, "");
}

export function readAccountApiKey(apiUrl: string): string | null {
  try {
    return localStorage.getItem(accountStorageKey(apiUrl)) || null;
  } catch {
    return null;
  }
}

export function writeAccountApiKey(apiKey: string, apiUrl: string): void {
  try {
    localStorage.setItem(accountStorageKey(apiUrl), apiKey);
  } catch {
    // A browser with storage disabled just loses the cache.
  }
}

export function clearAccountApiKey(apiUrl: string): void {
  try {
    localStorage.removeItem(accountStorageKey(apiUrl));
  } catch {
    // Storage disabled — nothing to clear.
  }
}

/** True when the key still exists on this API host (revoked keys and post-clean DB return false). */
export async function isCachedApiKeyValid(apiKey: string, apiUrl?: string): Promise<boolean> {
  try {
    const res = await fetch(`${apiBaseUrl(apiUrl)}/api/auth/api-keys/current/`, {
      headers: { "X-Api-Key": apiKey },
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function resolveAccountApiKey(apiUrl: string): Promise<string | null> {
  const cached = readAccountApiKey(apiUrl);
  if (!cached) return null;
  if (await isCachedApiKeyValid(cached, apiUrl)) return cached;
  clearAccountApiKey(apiUrl);
  return null;
}
