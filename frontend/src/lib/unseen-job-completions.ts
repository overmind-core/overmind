import { useSyncExternalStore } from "react";

import type { RunningJobItem, RunningJobKind } from "@/lib/running-jobs";

let unseen: RunningJobItem[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function getUnseenCompletions(): RunningJobItem[] {
  return unseen;
}

function subscribeUnseenCompletions(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

export function pushUnseenCompletions(jobs: RunningJobItem[]): void {
  if (jobs.length === 0) return;
  unseen = [...jobs, ...unseen].slice(0, 12);
  emit();
}

export function clearUnseenCompletions(): void {
  if (unseen.length === 0) return;
  unseen = [];
  emit();
}

export function clearUnseenCompletion(key: string): void {
  const next = unseen.filter((job) => job.key !== key);
  if (next.length === unseen.length) return;
  unseen = next;
  emit();
}

export function clearUnseenCompletionsForKinds(kinds: readonly RunningJobKind[]): void {
  if (kinds.length === 0) return;
  const kindSet = new Set(kinds);
  const next = unseen.filter((job) => !kindSet.has(job.kind));
  if (next.length === unseen.length) return;
  unseen = next;
  emit();
}

/** Which job kinds a visited path acknowledges as seen. */
const ROUTE_UNSEEN_KINDS: ReadonlyArray<{
  prefix: string;
  kinds: readonly RunningJobKind[];
}> = [
  { kinds: ["finetuning"], prefix: "/training" },
  { kinds: ["eval"], prefix: "/evaluations" },
  { kinds: ["optimizer"], prefix: "/optimiser" },
];

export function kindsForPathname(pathname: string): readonly RunningJobKind[] | null {
  for (const entry of ROUTE_UNSEEN_KINDS) {
    if (pathname === entry.prefix || pathname.startsWith(`${entry.prefix}/`)) {
      return entry.kinds;
    }
  }
  return null;
}

export function useUnseenCompletions(): RunningJobItem[] {
  return useSyncExternalStore(
    subscribeUnseenCompletions,
    getUnseenCompletions,
    getUnseenCompletions
  );
}

export function useUnseenCompletionKeys(): Set<string> {
  const items = useUnseenCompletions();
  return new Set(items.map((job) => job.key));
}
