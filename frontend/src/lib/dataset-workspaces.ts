import type { Dataset } from "@/openapi";

/** A workspace row. ``useFormat`` is the training format its training member declares,
 *  for a root whose own intent is not set. */
export type WorkspaceDataset = Dataset & { useFormat?: string };

export function datasetWorkspaces(datasets: Dataset[]): WorkspaceDataset[] {
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
      (member) => member.work && ["running", "queued", "accepted"].includes(member.work.status)
    );
    const latest = members.reduce((a, b) => (a.updatedAt > b.updatedAt ? a : b));
    return {
      ...root,
      roles: [...new Set(members.flatMap((member) => member.roles))],
      updatedAt: latest.updatedAt,
      useFormat:
        members.find((member) => member.intent === "train" && member.trainFormat)?.trainFormat ??
        "",
      ...(busy ? { work: busy.work } : {}),
    };
  });
}
