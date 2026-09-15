import { useCallback, useEffect, useRef, useState } from "react";

export type RecentItem = {
  id: string;
  type: "capability" | "project" | "resource";
  label: string;
  subtitle?: string;
  to: string;
  storedAt?: number;
  search?: Record<string, string>;
};

const STORAGE_KEY = "overmind:recents";
const MAX_RECENTS = 5;
const TTL_MS = 7 * 24 * 60 * 60 * 1000;

// Unguarded localStorage: CSR-only Vite app. SSR would need a window guard.
function readFromStorage(): RecentItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const cutoff = Date.now() - TTL_MS;
    return parsed.filter(
      (i) =>
        typeof i?.id === "string" &&
        typeof i?.to === "string" &&
        typeof i?.label === "string" &&
        (i?.type === "capability" || i?.type === "project" || i?.type === "resource") &&
        // No storedAt → expired.
        typeof i?.storedAt === "number" &&
        i.storedAt > cutoff
    );
  } catch {
    return [];
  }
}

function writeToStorage(items: RecentItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // ignore storage errors
  }
}

export function useRecentItems() {
  const [recents, setRecents] = useState<RecentItem[]>(readFromStorage);

  // The initialiser already read storage; skip the first render's redundant write.
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    writeToStorage(recents);
  }, [recents]);

  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) {
        setRecents(readFromStorage());
      }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const addRecent = useCallback((item: RecentItem) => {
    const stamped: RecentItem = { ...item, storedAt: Date.now() };
    setRecents((prev) => {
      const filtered = prev.filter((r) => r.id !== stamped.id);
      return [stamped, ...filtered].slice(0, MAX_RECENTS);
    });
  }, []);

  const removeRecent = useCallback((id: string) => {
    setRecents((prev) => prev.filter((r) => r.id !== id));
  }, []);

  return { addRecent, recents, removeRecent };
}
