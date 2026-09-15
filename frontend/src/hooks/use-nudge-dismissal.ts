import { useEffect, useState } from "react";

export function useNudgeDismissal(storageKey: string): {
  dismissed: boolean;
  dismiss: () => void;
} {
  const [dismissed, setDismissed] = useState(
    () => !!storageKey && localStorage.getItem(storageKey) === "1"
  );

  useEffect(() => {
    setDismissed(!!storageKey && localStorage.getItem(storageKey) === "1");
  }, [storageKey]);

  return {
    dismiss: () => {
      if (!storageKey) return;
      localStorage.setItem(storageKey, "1");
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
  const [dismissedAt, setDismissedAt] = useState(() =>
    Number(localStorage.getItem(storageKey) ?? 0)
  );

  useEffect(() => {
    setDismissedAt(Number(localStorage.getItem(storageKey) ?? 0));
  }, [storageKey]);

  return {
    dismiss: () => {
      localStorage.setItem(storageKey, String(currentCount));
      setDismissedAt(currentCount);
    },
    dismissed: currentCount <= dismissedAt,
  };
}
