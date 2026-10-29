import type { Dataset } from "@/openapi";

export function datasetWorkspaces(datasets: Dataset[]): Dataset[] {
  const byId = new Map(datasets.map((dataset) => [dataset.id, dataset]));
  const groups = new Map<string, Dataset[]>();
  for (const dataset of datasets) {
    const id = byId.has(dataset.workspaceId) ? dataset.workspaceId : dataset.id;
    const members = groups.get(id) ?? [];
    members.push(dataset);
    groups.set(id, members);
  }
  return [...groups].map(([id, members]) => {
    const root = byId.get(id)!;
    const busy = members.find(
      (member) =>
        member.work && ["running", "queued", "accepted", "paused"].includes(member.work.status)
    );
    const latest = members.reduce((a, b) => (a.updatedAt > b.updatedAt ? a : b));
    return {
      ...root,
      roles: [...new Set(members.flatMap((member) => member.roles))],
      updatedAt: latest.updatedAt,
      ...(busy ? { work: busy.work } : {}),
    };
  });
}
