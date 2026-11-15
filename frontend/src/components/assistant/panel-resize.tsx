import { useRef } from "react";

export const PANEL_WIDTH = { default: 416, max: 800, min: 320 };
const KEY_STEP = 16;

/** The panel's right edge: drag, arrow keys, Home and End resize it; a double click resets.
 *  A drag reports each width and ends with `null`; only `onCommit` is a kept width. */
export function PanelResize({
  panelId,
  width,
  onDrag,
  onCommit,
}: {
  panelId: string;
  width: number;
  onDrag: (width: number | null) => void;
  onCommit: (width: number) => void;
}) {
  const origin = useRef<{ x: number; width: number } | null>(null);
  const dragged = useRef(width);
  const clamp = (value: number) =>
    Math.round(Math.min(PANEL_WIDTH.max, Math.max(PANEL_WIDTH.min, value)));
  const end = () => {
    if (!origin.current) return;
    origin.current = null;
    onCommit(dragged.current);
    onDrag(null);
  };
  return (
    <div
      aria-controls={panelId}
      aria-label="Resize conversation"
      aria-orientation="vertical"
      aria-valuemax={PANEL_WIDTH.max}
      aria-valuemin={PANEL_WIDTH.min}
      aria-valuenow={width}
      className="assistant-resize-handle"
      onDoubleClick={() => onCommit(PANEL_WIDTH.default)}
      onKeyDown={(event) => {
        const next = {
          ArrowLeft: width - KEY_STEP,
          ArrowRight: width + KEY_STEP,
          End: PANEL_WIDTH.max,
          Home: PANEL_WIDTH.min,
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        onCommit(clamp(next));
      }}
      onLostPointerCapture={end}
      onPointerCancel={end}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        origin.current = { width, x: event.clientX };
        dragged.current = width;
        onDrag(width);
      }}
      onPointerMove={(event) => {
        if (!origin.current) return;
        dragged.current = clamp(origin.current.width + event.clientX - origin.current.x);
        onDrag(dragged.current);
      }}
      onPointerUp={end}
      role="separator"
      tabIndex={0}
    />
  );
}
