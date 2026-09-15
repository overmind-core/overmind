// Evaluated once at module load — safe in this CSR-only Vite app.
export const isMac = typeof navigator !== "undefined" && /Mac/i.test(navigator.userAgent);
