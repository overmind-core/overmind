import { crossingPoints, roundedRoute } from "./trajectoryRouting";

export type RenderedTrajectoryEdge = { id: string; source: string; target: string; path: string };
type Point = { x: number; y: number };
type Command = { text: string; start: Point; end: Point; line: boolean };

function commands(path: string): Command[] {
  let at = { x: 0, y: 0 };
  return [...path.matchAll(/([MLQC])([^MLQC]*)/g)].map((match) => {
    const values = (match[2].match(/[-+]?(?:\d*\.\d+|\d+)(?:e[-+]?\d+)?/gi) ?? []).map(Number);
    const end = { x: values.at(-2)!, y: values.at(-1)! };
    const command = { end, line: match[1] === "L", start: at, text: match[0].trim() };
    at = end;
    return command;
  });
}

export function renderedCrossings(edges: RenderedTrajectoryEdge[]) {
  const parsed = edges.map((edge) => ({ ...edge, commands: commands(edge.path) }));
  const segments = parsed.flatMap((edge) =>
    edge.commands
      .filter((c) => c.line)
      .map((c) => ({
        command: c,
        points: [c.start, c.end],
        source: edge.source,
        target: edge.target,
      }))
  );
  const hits = crossingPoints(segments);
  const byCommand = new Map(segments.map((s, i) => [s.command, hits[i]]));
  return new Map(
    parsed.map((edge) => {
      const bridges: string[] = [];
      const path = edge.commands
        .map((c) => {
          const crossings = byCommand.get(c);
          if (!crossings?.length) return c.text;
          const horizontal = c.start.y === c.end.y;
          const direction = Math.sign(horizontal ? c.end.x - c.start.x : c.end.y - c.start.y);
          for (const p of crossings) {
            bridges.push(
              horizontal
                ? `M ${p.x - 6 * direction} ${p.y} C ${p.x - 3 * direction} ${p.y - 8} ${p.x + 3 * direction} ${p.y - 8} ${p.x + 6 * direction} ${p.y}`
                : `M ${p.x} ${p.y - 6 * direction} C ${p.x + 8} ${p.y - 3 * direction} ${p.x + 8} ${p.y + 3 * direction} ${p.x} ${p.y + 6 * direction}`
            );
          }
          return roundedRoute([c.start, c.end], crossings).replace(/^M [^ ]+ [^ ]+ /, "");
        })
        .join(" ");
      return [edge.id, { bridges, original: edge.path, path }] as const;
    })
  );
}
