type Listener = (deltaCredits: number) => void;

const listeners = new Set<Listener>();

export function onCreditsDelta(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
