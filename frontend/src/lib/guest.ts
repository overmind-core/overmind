import { localStore } from "./storage";

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
  return localStore.get(GUEST_FLAG) === "1";
}

export function getGuestProjectId(): string | null {
  return localStore.get(GUEST_PROJECT);
}

export function markGuestSession(projectId: string): void {
  localStore.set(GUEST_FLAG, "1");
  localStore.set(GUEST_PROJECT, projectId);
}

export function clearGuestSession(): void {
  localStore.remove(GUEST_FLAG);
  localStore.remove(GUEST_PROJECT);
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
