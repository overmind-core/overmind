let required = false;
const listeners = new Set<() => void>();

export function requireSessionRecovery() {
  if (required) return;
  required = true;
  for (const listener of listeners) listener();
}

export function dismissSessionRecovery() {
  required = false;
  for (const listener of listeners) listener();
}

export const sessionRecoveryRequired = () => required;

export function subscribeSessionRecovery(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
