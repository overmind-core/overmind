// Hidden tabs must release HTTP/1 connections so ordinary API reads can proceed.
export function whilePageVisible(start: () => () => void) {
  let stop: (() => void) | undefined;
  const update = () => {
    if (document.visibilityState === "hidden") {
      stop?.();
      stop = undefined;
    } else if (!stop) {
      stop = start();
    }
  };
  document.addEventListener("visibilitychange", update);
  update();
  return () => {
    document.removeEventListener("visibilitychange", update);
    stop?.();
  };
}
