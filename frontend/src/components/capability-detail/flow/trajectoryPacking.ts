export type LayoutBlock = { width: number; height: number };

/** Compare ordered grids using their actual bounds. Stable column gutters are
 * necessary for tools to share an input rail without running through siblings. */
export function packTrajectoryBlocks(blocks: LayoutBlock[], gap = 120, columnsOnly?: number) {
  if (!blocks.length)
    return { height: 0, items: [] as { x: number; y: number; rowBottom: number }[], width: 0 };
  const candidates = (
    columnsOnly ? [columnsOnly] : Array.from({ length: blocks.length }, (_, i) => i + 1)
  ).map((columns) => {
    const widths = Array.from({ length: columns }, (_, c) =>
      Math.max(...blocks.filter((_, i) => i % columns === c).map((b) => b.width))
    );
    const heights = Array.from({ length: Math.ceil(blocks.length / columns) }, (_, r) =>
      Math.max(...blocks.slice(r * columns, (r + 1) * columns).map((b) => b.height))
    );
    const offset = (values: number[], index: number) =>
      values.slice(0, index).reduce((sum, v) => sum + v + gap, 0);
    const width = offset(widths, widths.length) - gap,
      height = offset(heights, heights.length) - gap;
    const items = blocks.map((_, i) => ({
      rowBottom: offset(heights, Math.floor(i / columns)) + heights[Math.floor(i / columns)],
      x: offset(widths, i % columns),
      y: offset(heights, Math.floor(i / columns)),
    }));
    const score = width * height * (1 + Math.log(width / height / 1.5) ** 2);
    return { height, items, score, width };
  });
  return candidates.sort((a, b) => a.score - b.score || a.width - b.width)[0];
}
