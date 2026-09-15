export type DiffOp = { kind: "same" | "add" | "del"; text: string };

const TOKEN = /\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu;
const MAX_CELLS = 400_000;

function tokens(text: string): string[] {
  return text.match(TOKEN) ?? [];
}

function merge(ops: DiffOp[]): DiffOp[] {
  const out: DiffOp[] = [];
  for (const op of ops) {
    const last = out.at(-1);
    if (last && last.kind === op.kind) last.text += op.text;
    else out.push({ ...op });
  }
  return out;
}

/** Common prefix and suffix only: the fallback when the texts are too long
 *  for the word alignment. */
function edgeDiff(before: string, after: string): DiffOp[] {
  let start = 0;
  const max = Math.min(before.length, after.length);
  while (start < max && before[start] === after[start]) start++;
  let end = 0;
  while (end < max - start && before[before.length - 1 - end] === after[after.length - 1 - end])
    end++;
  return merge([
    { kind: "same", text: after.slice(0, start) },
    { kind: "del", text: before.slice(start, before.length - end) },
    { kind: "add", text: after.slice(start, after.length - end) },
    { kind: "same", text: after.slice(after.length - end) },
  ]).filter((op) => op.text);
}

/** Word-level diff of two texts, as runs of kept, removed and added text. */
export function diffText(before: string, after: string): DiffOp[] {
  if (before === after) return before ? [{ kind: "same", text: after }] : [];
  const a = tokens(before);
  const b = tokens(after);
  if (a.length * b.length > MAX_CELLS) return edgeDiff(before, after);
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const lcs = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * width + j] =
        a[i] === b[j]
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1]);
    }
  }
  const ops: DiffOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: "same", text: a[i] });
      i++;
      j++;
    } else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) {
      ops.push({ kind: "del", text: a[i] });
      i++;
    } else {
      ops.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  for (; i < n; i++) ops.push({ kind: "del", text: a[i] });
  for (; j < m; j++) ops.push({ kind: "add", text: b[j] });
  return merge(ops);
}
