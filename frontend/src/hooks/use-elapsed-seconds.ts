import { useEffect, useState } from "react";

/** Whole seconds since `since`, ticking once a second; null while there is no start. */
export function useElapsedSeconds(since?: Date): number | null {
  const [now, setNow] = useState(() => Date.now());
  const sinceMs = since?.getTime();
  useEffect(() => {
    if (sinceMs == null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [sinceMs]);
  if (sinceMs == null) return null;
  return Math.max(0, Math.floor((now - sinceMs) / 1000));
}
