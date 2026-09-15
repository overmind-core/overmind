const GUEST_FLAG = "guest_session";
const GUEST_PROJECT = "guest_project_id";

export const GUEST_UPGRADE_CODE = "guest_upgrade_required";

export function isGuestAllowedPath(pathname: string): boolean {
  if (pathname === "/") return true;
  return /^\/capabilities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    pathname
  );
}

export function hasGuestSession(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(GUEST_FLAG) === "1";
}

export function getGuestProjectId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(GUEST_PROJECT);
}

export function markGuestSession(projectId: string): void {
  localStorage.setItem(GUEST_FLAG, "1");
  localStorage.setItem(GUEST_PROJECT, projectId);
}

export function clearGuestSession(): void {
  localStorage.removeItem(GUEST_FLAG);
  localStorage.removeItem(GUEST_PROJECT);
}

type Listener = () => void;
const listeners = new Set<Listener>();
// The prompt is module state, not dialog state: the layout requests it while
// redirecting a guest, and the dialog mounts (and remounts) only afterwards.
let prompted = false;

export function onGuestUpgrade(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isGuestPromptOpen(): boolean {
  return prompted;
}

export function emitGuestUpgrade(): void {
  if (prompted) return;
  prompted = true;
  for (const listener of listeners) listener();
}

export function dismissGuestPrompt(): void {
  if (!prompted) return;
  prompted = false;
  for (const listener of listeners) listener();
}

export class GuestUpgradeError extends Error {
  override name = "GuestUpgradeError";

  constructor() {
    super("Create an account to continue.");
  }
}
