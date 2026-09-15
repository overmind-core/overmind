/**
 * `react-remove-scroll` (Radix modal `Dialog`) cancels `wheel`/`touchmove`
 * whose target sits outside the dialog subtree, which kills wheel scrolling in
 * content portalled to `<body>`. Stopping those events before the document
 * listener sees them restores native scroll. `PopoverContent` attaches this
 * already; Radix `Select` / `DropdownMenu` need no equivalent.
 */
export function escapeScrollLock(node: HTMLElement | null): (() => void) | undefined {
  if (!node) {
    return undefined;
  }
  const stopPropagation = (event: Event) => event.stopPropagation();
  node.addEventListener("wheel", stopPropagation, { passive: true });
  node.addEventListener("touchmove", stopPropagation, { passive: true });
  return () => {
    node.removeEventListener("wheel", stopPropagation);
    node.removeEventListener("touchmove", stopPropagation);
  };
}
