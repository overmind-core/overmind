type Point = { x: number; y: number };

export const CORNER_RADIUS = 12;
export const CONDITION_WIDTH = 96;
export const CONDITION_HEIGHT = 28;
export const CONDITION_SPACE = 72;

export function outcomeRoute({ source, target }: { source: Point; target: Point }): Point[] {
  if (source.x === target.x) return [source, target];
  const forkY = source.y + 24;
  return [source, { x: source.x, y: forkY }, { x: target.x, y: forkY }, target];
}

/** Bridges belong to the later-painted edge; endpoints and shared-node joins remain connected. */
export function crossingPoints(
  routes: { points: Point[]; source: string; target: string }[]
): Point[][] {
  const result: Point[][] = routes.map(() => []);
  for (let i = 0; i < routes.length; i++) {
    const a = routes[i];
    for (let j = 0; j < i; j++) {
      const b = routes[j];
      if (
        a.source === b.source ||
        a.target === b.target ||
        a.source === b.target ||
        a.target === b.source
      )
        continue;
      for (let ai = 1; ai < a.points.length; ai++) {
        for (let bi = 1; bi < b.points.length; bi++) {
          const a0 = a.points[ai - 1],
            a1 = a.points[ai];
          const b0 = b.points[bi - 1],
            b1 = b.points[bi];
          const horizontal = a0.y === a1.y ? [a0, a1] : b0.y === b1.y ? [b0, b1] : null;
          const vertical = a0.x === a1.x ? [a0, a1] : b0.x === b1.x ? [b0, b1] : null;
          if (!horizontal || !vertical) continue;
          const p = { x: vertical[0].x, y: horizontal[0].y };
          const margin = CORNER_RADIUS + 8;
          if (
            p.x <= Math.min(horizontal[0].x, horizontal[1].x) + margin ||
            p.x >= Math.max(horizontal[0].x, horizontal[1].x) - margin ||
            p.y <= Math.min(vertical[0].y, vertical[1].y) + margin ||
            p.y >= Math.max(vertical[0].y, vertical[1].y) - margin
          )
            continue;
          if (!result[i].some((hit) => Math.hypot(hit.x - p.x, hit.y - p.y) < 16))
            result[i].push(p);
        }
      }
    }
  }
  return result;
}

export function roundedRoute(points: Point[], crossings: Point[] = []): string {
  if (!points.length) return "";
  let current = points[0];
  const path = [`M ${current.x} ${current.y}`];
  const line = (to: Point) => {
    const horizontal = current.y === to.y;
    const direction = Math.sign(horizontal ? to.x - current.x : to.y - current.y);
    const length = Math.hypot(to.x - current.x, to.y - current.y);
    const hits = crossings
      .filter(
        (p) =>
          (horizontal ? p.y === current.y : p.x === current.x) &&
          (horizontal ? p.x - current.x : p.y - current.y) * direction > 8 &&
          (horizontal ? p.x - current.x : p.y - current.y) * direction < length - 8
      )
      .sort((a, b) => (horizontal ? (a.x - b.x) * direction : (a.y - b.y) * direction));
    for (const p of hits) {
      if (horizontal)
        path.push(
          `L ${p.x - 6 * direction} ${p.y} C ${p.x - 3 * direction} ${p.y - 8} ${p.x + 3 * direction} ${p.y - 8} ${p.x + 6 * direction} ${p.y}`
        );
      else
        path.push(
          `L ${p.x} ${p.y - 6 * direction} C ${p.x + 8} ${p.y - 3 * direction} ${p.x + 8} ${p.y + 3 * direction} ${p.x} ${p.y + 6 * direction}`
        );
    }
    path.push(`L ${to.x} ${to.y}`);
    current = to;
  };
  for (let i = 1; i < points.length; i++) {
    const point = points[i];
    if (i === points.length - 1) {
      line(point);
      break;
    }
    const before = points[i - 1],
      after = points[i + 1];
    const radius = Math.min(
      CORNER_RADIUS,
      Math.hypot(point.x - before.x, point.y - before.y) / 2,
      Math.hypot(after.x - point.x, after.y - point.y) / 2
    );
    const enter = {
      x: point.x - Math.sign(point.x - before.x) * radius,
      y: point.y - Math.sign(point.y - before.y) * radius,
    };
    const leave = {
      x: point.x + Math.sign(after.x - point.x) * radius,
      y: point.y + Math.sign(after.y - point.y) * radius,
    };
    line(enter);
    path.push(`Q ${point.x} ${point.y} ${leave.x} ${leave.y}`);
    current = leave;
  }
  return path.join(" ");
}

export type RouteObstacle = { x: number; y: number; width: number; height: number };

/** Shortest orthogonal corridor around cards and labels, with a small bend cost.
 * This runs on layout geometry; it needs no DOM, screenshot or rendering pass. */
export function routeAroundBoxes(start: Point, end: Point, boxes: RouteObstacle[]): Point[] {
  const padding = 12;
  const obstacles = boxes.map((b) => ({
    bottom: b.y + b.height + padding,
    left: b.x - padding,
    right: b.x + b.width + padding,
    top: b.y - padding,
  }));
  const xs = [...new Set([start.x, end.x, ...obstacles.flatMap((b) => [b.left, b.right])])].sort(
    (a, b) => a - b
  );
  const ys = [...new Set([start.y, end.y, ...obstacles.flatMap((b) => [b.top, b.bottom])])].sort(
    (a, b) => a - b
  );
  const clear = (a: Point, b: Point) =>
    !obstacles.some((r) =>
      a.y === b.y
        ? a.y > r.top &&
          a.y < r.bottom &&
          Math.max(a.x, b.x) > r.left &&
          Math.min(a.x, b.x) < r.right
        : a.x > r.left &&
          a.x < r.right &&
          Math.max(a.y, b.y) > r.top &&
          Math.min(a.y, b.y) < r.bottom
    );
  const index = (x: number, y: number) => y * xs.length + x;
  const point = (i: number) => ({ x: xs[i % xs.length], y: ys[Math.floor(i / xs.length)] });
  const first = index(xs.indexOf(start.x), ys.indexOf(start.y));
  const last = index(xs.indexOf(end.x), ys.indexOf(end.y));
  const queue = [{ cost: 0, key: first * 3 }],
    distance = new Map([[first * 3, 0]]),
    previous = new Map<number, number>();
  let found: number | undefined;
  while (queue.length) {
    const current = queue.shift()!;
    if (current.cost !== distance.get(current.key)) continue;
    const cell = Math.floor(current.key / 3),
      direction = current.key % 3;
    if (cell === last) {
      found = current.key;
      break;
    }
    const x = cell % xs.length,
      y = Math.floor(cell / xs.length),
      a = point(cell);
    for (const [nx, ny, dir] of [
      [x - 1, y, 1],
      [x + 1, y, 1],
      [x, y - 1, 2],
      [x, y + 1, 2],
    ]) {
      if (nx < 0 || nx >= xs.length || ny < 0 || ny >= ys.length) continue;
      const next = index(nx, ny),
        b = point(next);
      if (!clear(a, b)) continue;
      const key = next * 3 + dir,
        cost =
          current.cost +
          Math.abs(a.x - b.x) +
          Math.abs(a.y - b.y) +
          (direction && direction !== dir ? 32 : 0);
      if (cost >= (distance.get(key) ?? Infinity)) continue;
      distance.set(key, cost);
      previous.set(key, current.key);
      // Small frontier; binary insertion avoids a dependency for this queue.
      let lo = 0,
        hi = queue.length;
      while (lo < hi) {
        const mid = (lo + hi) >>> 1;
        if (queue[mid].cost < cost) lo = mid + 1;
        else hi = mid;
      }
      queue.splice(lo, 0, { cost, key });
    }
  }
  if (found === undefined) return outcomeRoute({ source: start, target: end });
  const path: Point[] = [];
  for (let key: number | undefined = found; key !== undefined; key = previous.get(key))
    path.push(point(Math.floor(key / 3)));
  path.reverse();
  return path.filter(
    (p, i) =>
      !i ||
      i === path.length - 1 ||
      !(
        (path[i - 1].x === p.x && p.x === path[i + 1].x) ||
        (path[i - 1].y === p.y && p.y === path[i + 1].y)
      )
  );
}
