// Reading window.localStorage itself throws a SecurityError when the browser blocks
// site data, so every storage access in the Console goes through these wrappers.
function guarded(area: () => Storage) {
  return {
    get(key: string): string | null {
      try {
        return area().getItem(key);
      } catch {
        return null;
      }
    },
    remove(key: string): void {
      try {
        area().removeItem(key);
      } catch {}
    },
    /** Returns false when the browser refused the write. */
    set(key: string, value: string): boolean {
      try {
        area().setItem(key, value);
        return true;
      } catch {
        return false;
      }
    },
  };
}

export const localStore = guarded(() => window.localStorage);
export const sessionStore = guarded(() => window.sessionStorage);
