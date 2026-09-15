export const CHUNK_RELOAD_KEY = "overmind-chunk-reload";

const CHUNK_LOAD_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /importing a module script failed/i,
  /error loading dynamically imported module/i,
  /chunkloaderror/i,
  // SPA fallback serves index.html for missing hashed chunks.
  /expected a javascript.*text\/html/i,
];

function errorText(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && "message" in err) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return "";
}

export function isChunkLoadError(err: unknown): boolean {
  const message = errorText(err);
  if (!message) return false;
  return CHUNK_LOAD_PATTERNS.some((pattern) => pattern.test(message));
}

export function hasChunkReloadFlag(): boolean {
  try {
    return sessionStorage.getItem(CHUNK_RELOAD_KEY) !== null;
  } catch {
    return false;
  }
}

export function tryReloadOnceOnChunkError(err: unknown): boolean {
  if (!isChunkLoadError(err)) return false;
  try {
    if (hasChunkReloadFlag()) return false;
    sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}

export function clearChunkReloadFlag(): void {
  try {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY);
  } catch {
    // sessionStorage unavailable (private mode, etc.)
  }
}

export function withChunkReloadRetry<T>(loader: () => Promise<T>): () => Promise<T> {
  return () =>
    loader()
      .then((value) => {
        clearChunkReloadFlag();
        return value;
      })
      .catch((err) => {
        if (tryReloadOnceOnChunkError(err)) {
          return new Promise<T>(() => {});
        }
        throw err;
      });
}
