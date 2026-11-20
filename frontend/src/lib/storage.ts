// Reading window.localStorage itself throws a SecurityError when the browser blocks
// site data, so every storage access in the Console goes through these wrappers.
function guarded(area: () => Storage) {
  // A refused write lives for this page and shadows any older stored copy, so a
  // rotated token never falls back to the one it replaced.
  const refused = new Map<string, string>();
  return {
    get(key: string): string | null {
      if (refused.has(key)) return refused.get(key) ?? null;
      try {
        return area().getItem(key);
      } catch {
        return null;
      }
    },
    remove(key: string): void {
      refused.delete(key);
      try {
        area().removeItem(key);
      } catch {}
    },
    /** Returns false when the browser refused the write; the value then lasts for this page only. */
    set(key: string, value: string): boolean {
      try {
        area().setItem(key, value);
        refused.delete(key);
        return true;
      } catch {
        refused.set(key, value);
        return false;
      }
    },
  };
}

export const localStore = guarded(() => window.localStorage);
export const sessionStore = guarded(() => window.sessionStorage);
