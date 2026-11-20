import { useCallback, useState } from "react";

import { localStore } from "@/lib/storage";
/** `useState` persisted to `localStorage` under `key`; a refused write keeps in-memory state. */
export function usePersistedState<T>(
  key: string,
  defaultValue: T
): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStore.get(key);
      return raw != null ? (JSON.parse(raw) as T) : defaultValue;
    } catch {
      return defaultValue;
    }
  });

  const setPersisted = useCallback(
    (value: T | ((prev: T) => T)) => {
      setState((prev) => {
        const next = typeof value === "function" ? (value as (p: T) => T)(prev) : value;
        localStore.set(key, JSON.stringify(next));
        return next;
      });
    },
    [key]
  );

  return [state, setPersisted];
}
