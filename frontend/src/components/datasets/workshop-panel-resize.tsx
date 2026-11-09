import { useEffect, useRef, useState } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export const WORKSHOP_PANEL_WIDTH = 416;

export function WorkshopPanelResize({
  panelId,
  width,
  onWidthChange,
  onResizingChange,
}: {
  panelId: string;
  width: number;
  onWidthChange: (width: number) => void;
  onResizingChange: (resizing: boolean) => void;
}) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  const [maximum, setMaximum] = useState(800);
  const minimum = Math.min(320, maximum);
  const clamp = (value: number) => Math.min(maximum, Math.max(minimum, value));

  useEffect(() => {
    const container = handle.current?.parentElement?.parentElement;
    if (!container) return;
    const measure = () => {
      if (container.clientWidth) setMaximum(Math.min(800, container.clientWidth - 24));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (width > maximum) onWidthChange(maximum);
  }, [maximum, onWidthChange, width]);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          aria-controls={panelId}
          aria-label="Chat panel width"
          aria-orientation="vertical"
          aria-valuemax={maximum}
          aria-valuemin={minimum}
          aria-valuenow={width}
          aria-valuetext={`${width} pixels`}
          className="workshop-resize-handle"
          onDoubleClick={() => onWidthChange(clamp(WORKSHOP_PANEL_WIDTH))}
          onKeyDown={(event) => {
            const step = event.shiftKey ? 48 : 16;
            const value =
              event.key === "ArrowLeft"
                ? width - step
                : event.key === "ArrowRight"
                  ? width + step
                  : event.key === "Home"
                    ? minimum
                    : event.key === "End"
                      ? maximum
                      : null;
            if (value === null) return;
            event.preventDefault();
            onWidthChange(clamp(value));
          }}
          onLostPointerCapture={() => {
            drag.current = null;
            onResizingChange(false);
          }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.focus();
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { pointerId: event.pointerId, width, x: event.clientX };
            onResizingChange(true);
          }}
          onPointerMove={(event) => {
            const start = drag.current;
            if (start?.pointerId !== event.pointerId) return;
            onWidthChange(clamp(start.width + event.clientX - start.x));
          }}
          onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
          ref={handle}
          role="separator"
          tabIndex={0}
        />
      </TooltipTrigger>
      <TooltipContent>Drag or use arrow keys to resize · Double-click to reset</TooltipContent>
    </Tooltip>
  );
}
