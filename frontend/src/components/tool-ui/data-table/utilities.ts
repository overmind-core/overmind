export function sortData<T, K extends Extract<keyof T, string>>(
  data: T[],
  key: K,
  direction: "asc" | "desc",
  locale?: string
): T[] {
  const get = (obj: T, k: K): unknown => (obj as Record<string, unknown>)[k];
  const collator = new Intl.Collator(locale, {
    numeric: true,
    sensitivity: "base",
  });
  return [...data].sort((a, b) => {
    const aVal = get(a, key);
    const bVal = get(b, key);

    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return 1;
    if (bVal == null) return -1;

    if (typeof aVal === "number" && typeof bVal === "number") {
      return direction === "asc" ? aVal - bVal : bVal - aVal;
    }

    if (aVal instanceof Date && bVal instanceof Date) {
      const diff = aVal.getTime() - bVal.getTime();
      return direction === "asc" ? diff : -diff;
    }

    if (typeof aVal === "boolean" && typeof bVal === "boolean") {
      const diff = aVal === bVal ? 0 : aVal ? 1 : -1;
      return direction === "asc" ? diff : -diff;
    }

    if (Array.isArray(aVal) && Array.isArray(bVal)) {
      const diff = aVal.length - bVal.length;
      return direction === "asc" ? diff : -diff;
    }

    if (typeof aVal === "string" && typeof bVal === "string") {
      const numA = parseNumericLike(aVal);
      const numB = parseNumericLike(bVal);
      if (numA != null && numB != null) {
        const diff = numA - numB;
        return direction === "asc" ? diff : -diff;
      }

      if (/^\d{4}-\d{2}-\d{2}/.test(aVal) && /^\d{4}-\d{2}-\d{2}/.test(bVal)) {
        const da = new Date(aVal).getTime();
        const db = new Date(bVal).getTime();
        const diff = da - db;
        return direction === "asc" ? diff : -diff;
      }
    }

    const aStr = String(aVal);
    const bStr = String(bVal);
    const comparison = collator.compare(aStr, bStr);
    return direction === "asc" ? comparison : -comparison;
  });
}

export function getRowIdentifier(
  row: Record<string, string | number | boolean | null | (string | number | boolean | null)[]>,
  identifierKey?: string
): string {
  const candidate =
    (identifierKey ? row[identifierKey] : undefined) ??
    (row as Record<string, unknown>).name ??
    (row as Record<string, unknown>).title ??
    (row as Record<string, unknown>).id;

  if (candidate == null) {
    return "";
  }

  if (Array.isArray(candidate)) {
    return candidate.map((v) => (v === null ? "null" : String(v))).join(", ");
  }

  return String(candidate).trim();
}

function stableStringify(value: unknown): string {
  if (value == null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
      a.localeCompare(b)
    );
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(String(value));
}

function hashString(value: string): string {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 33) ^ value.charCodeAt(i);
  }
  return (hash >>> 0).toString(36);
}

export function createDataTableRowKeys(
  rows: Array<Record<string, unknown>>,
  identifierKey?: string
): string[] {
  const canonicalRows = rows.map((row) => stableStringify(row));

  const baseKeys = rows.map((row, index) => {
    const identifier = getRowIdentifier(
      row as Record<
        string,
        string | number | boolean | null | (string | number | boolean | null)[]
      >,
      identifierKey
    );

    if (identifier) {
      return `id:${identifier}`;
    }

    return `row:${hashString(canonicalRows[index])}`;
  });

  const baseCounts = new Map<string, number>();
  baseKeys.forEach((key) => {
    baseCounts.set(key, (baseCounts.get(key) ?? 0) + 1);
  });

  const usedKeys = new Map<string, number>();

  return rows.map((_row, index) => {
    const baseKey = baseKeys[index];
    if ((baseCounts.get(baseKey) ?? 0) === 1) {
      return baseKey;
    }

    const rowFingerprint = hashString(canonicalRows[index]);
    let disambiguatedKey = `${baseKey}::${rowFingerprint}`;

    const seenCount = usedKeys.get(disambiguatedKey) ?? 0;
    usedKeys.set(disambiguatedKey, seenCount + 1);
    if (seenCount > 0) {
      disambiguatedKey = `${disambiguatedKey}::d${seenCount + 1}`;
    }

    return disambiguatedKey;
  });
}

function sanitizeDomIdToken(value: string): string {
  return encodeURIComponent(value).replace(/%/g, "_");
}

export function getDataTableMobileDescriptionId(surfaceId: string): string {
  return `${sanitizeDomIdToken(surfaceId)}-mobile-table-description`;
}

export function parseNumericLike(input: string): number | null {
  let s = input.replace(/[\u00A0\u202F\s]/g, "").trim();
  if (!s) return null;

  s = s.replace(/^\((.*)\)$/g, "-$1");

  s = s.replace(/[%$€£¥₩₹₽₺₪₫฿₦₴₡₲₵₸]/g, "");

  function hasGroupedThousands(value: string, sep: "," | "."): boolean {
    const unsigned = value.replace(/^[+-]/, "");
    const parts = unsigned.split(sep);
    if (parts.length < 2) return false;
    if (parts.some((part) => part.length === 0)) return false;
    if (!/^\d{1,3}$/.test(parts[0])) return false;
    if (parts[0] === "0") return false;
    return parts.slice(1).every((part) => /^\d{3}$/.test(part));
  }

  const lastComma = s.lastIndexOf(",");
  const lastDot = s.lastIndexOf(".");
  if (lastComma !== -1 && lastDot !== -1) {
    const decimalSep = lastComma > lastDot ? "," : ".";
    const thousandSep = decimalSep === "," ? "." : ",";
    s = s.split(thousandSep).join("");
    s = s.replace(decimalSep, ".");
  } else if (lastComma !== -1) {
    if (hasGroupedThousands(s, ",")) {
      s = s.replace(/,/g, "");
    } else {
      const frac = s.length - lastComma - 1;
      if (frac >= 1 && frac <= 3) s = s.replace(/,/g, ".");
      else s = s.replace(/,/g, "");
    }
  } else if (lastDot !== -1) {
    if (hasGroupedThousands(s, ".")) {
      s = s.replace(/\./g, "");
    } else if ((s.match(/\./g) || []).length > 1) {
      s = s.replace(/\./g, "");
    }
  }

  const compactMatch = s.match(/^([+-]?\d+\.?\d*|\d*\.\d+)([KMBTPG]B?|B)$/i);
  if (compactMatch) {
    const baseNum = Number(compactMatch[1]);
    if (Number.isNaN(baseNum)) return null;

    const suffix = compactMatch[2].toUpperCase();

    if (suffix === "B") {
      const isLikelyBytes = Number.isInteger(baseNum) && baseNum < 1024;
      return isLikelyBytes ? baseNum : baseNum * 1e9;
    }

    const multipliers: Record<string, number> = {
      G: 1e9,
      GB: 1024 ** 3, // Giga
      K: 1e3,
      KB: 1024, // Kilo: metric vs binary
      M: 1e6,
      MB: 1024 ** 2, // Mega
      P: 1e15,
      PB: 1024 ** 5, // Peta
      T: 1e12,
      TB: 1024 ** 4, // Tera
    };

    return baseNum * (multipliers[suffix] ?? 1);
  }

  if (/^[+-]?(?:\d+\.?\d*|\d*\.\d+)$/.test(s)) {
    const n = Number(s);
    return Number.isNaN(n) ? null : n;
  }
  return null;
}
