import { useCallback, useState } from "react";

/** `useState` persisted to `localStorage` under `key`. Storage failures (quota,
 * privacy mode, SSR) degrade to in-memory state. */
export function usePersistedState<T>(
  key: string,
  defaultValue: T
): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw != null ? (JSON.parse(raw) as T) : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  const setPersisted = useCallback(
    (value: T | ((prev: T) => T)) => {
      setState((prev) => {
        const next = typeof value === "function" ? (value as (p: T) => T)(prev) : value;
        try {
          localStorage.setItem(key, JSON.stringify(next));
        } catch {
          // ignore quota / privacy-mode / SSR errors — fall back to in-memory
        }
        return next;
      });
    },
    [key]
  );

  return [state, setPersisted];
}
