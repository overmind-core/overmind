const CHUNK_LOAD_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /importing a module script failed/i,
  /error loading dynamically imported module/i,
  /chunkloaderror/i,
  /loading chunk \S+ failed/i,
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
