import { useEffect, useState } from "react";

import { localStore } from "@/lib/storage";

export function useNudgeDismissal(storageKey: string): {
  dismissed: boolean;
  dismiss: () => void;
} {
  const [dismissed, setDismissed] = useState(
    () => !!storageKey && localStore.get(storageKey) === "1"
  );

  useEffect(() => {
    setDismissed(!!storageKey && localStore.get(storageKey) === "1");
  }, [storageKey]);

  return {
    dismiss: () => {
      if (!storageKey) return;
      localStore.set(storageKey, "1");
      setDismissed(true);
    },
    dismissed: !storageKey || dismissed,
  };
}

/** Stores the count at dismiss time, so the nudge reappears only once a newer
 * count exceeds it. */
export function useNudgeDismissalCount(
  storageKey: string,
  currentCount: number
): {
  dismissed: boolean;
  dismiss: () => void;
} {
  const [dismissedAt, setDismissedAt] = useState(() => Number(localStore.get(storageKey) ?? 0));

  useEffect(() => {
    setDismissedAt(Number(localStore.get(storageKey) ?? 0));
  }, [storageKey]);

  return {
    dismiss: () => {
      localStore.set(storageKey, String(currentCount));
      setDismissedAt(currentCount);
    },
    dismissed: currentCount <= dismissedAt,
  };
}
