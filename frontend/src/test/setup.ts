// Node 22+ can expose a broken window.localStorage under jsdom without
// --localstorage-file; guest and client tests need a working store.
function ensureLocalStorage(): void {
  try {
    const storage = globalThis.localStorage;
    if (storage && typeof storage.getItem === "function") {
      storage.setItem("__probe__", "1");
      storage.removeItem("__probe__");
      return;
    }
  } catch {
    // fall through to the in-memory polyfill
  }

  const store = new Map<string, string>();
  const storage = {
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
  };

  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  if (typeof globalThis.window !== "undefined") {
    Object.defineProperty(globalThis.window, "localStorage", {
      configurable: true,
      value: storage,
    });
  }
}

ensureLocalStorage();
