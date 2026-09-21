// Match the shared splitter's half-up target before grouping and deduplication.
export function evaluationRows(rows: number, percent: number): number {
  if (rows < 2) return 0;
  const rounded = Math.floor((rows * percent + 50) / 100);
  return Math.min(Math.max(rounded, 1), rows - 1);
}
