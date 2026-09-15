import { useEffect, useState } from "react";

import { resolveAccountApiKey } from "@/lib/project-api-key";

export function useValidatedAccountApiKey(apiUrl: string) {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    void resolveAccountApiKey(apiUrl).then((key) => {
      if (!cancelled) {
        setApiKey(key);
        setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  return { apiKey, ready, setApiKey };
}
