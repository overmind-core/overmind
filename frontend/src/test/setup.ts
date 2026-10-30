import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll } from "vitest";

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

const ENTRY_KINDS = new Map(
  Array.from(
    readFileSync(resolve(process.cwd(), "../tests/coverage_map.py"), "utf8").matchAll(
      /"([\w-]+)": Entry\(\s*S\d,\s*(PROMISE|INVARIANT|CONTRACT|LOGIC)/g
    ),
    ([, slug, kind]) => [slug, kind]
  )
);

// Every test file names the one tests/coverage_map.py entry it covers, as pytest
// collection requires of backend tests.
// biome-ignore lint/correctness/noEmptyPattern: vitest 4 passes the suite second and requires a destructured context first.
beforeAll(({}, suite) => {
  const path = suite.file.filepath;
  const covers = Array.from(
    readFileSync(path, "utf8").matchAll(/^\/\/ covers: (\S+)$/gm),
    ([, slug]) => slug
  );
  if (covers.length !== 1) {
    throw new Error(
      `${path}: add one "// covers: <entry>" line naming its tests/coverage_map.py entry`
    );
  }
  const kind = ENTRY_KINDS.get(covers[0]);
  if (kind === undefined) {
    throw new Error(`${path}: "${covers[0]}" is not in tests/coverage_map.py`);
  }
  if (kind === "PROMISE") {
    throw new Error(`${path}: a promise is covered only by its journey`);
  }
});
