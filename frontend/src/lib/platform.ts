// Evaluated once at module load — safe in this CSR-only Vite app.
export const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.userAgent);

// iPadOS can identify as a Mac; touch points distinguish it from desktop Safari.
export const isMobileBrowser = () =>
  /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) ||
  (/Macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
