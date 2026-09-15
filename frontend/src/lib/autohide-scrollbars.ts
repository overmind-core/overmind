/**
 * `styles.css` keys scrollbar-thumb opacity off `data-scrolling`. Scroll events
 * don't bubble, so the listener MUST be capture-phase to see every container.
 */

const IDLE_MS = 700;
const timers = new WeakMap<Element, number>();

function onScroll(event: Event) {
  const target = event.target;
  const el =
    target instanceof Document ? target.documentElement : target instanceof Element ? target : null;
  if (!el) return;

  el.setAttribute("data-scrolling", "");
  const pending = timers.get(el);
  if (pending !== undefined) window.clearTimeout(pending);
  timers.set(
    el,
    window.setTimeout(() => {
      el.removeAttribute("data-scrolling");
      timers.delete(el);
    }, IDLE_MS)
  );
}

export function installAutohideScrollbars() {
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
}
