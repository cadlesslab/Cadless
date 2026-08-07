/** A draggable vertical divider between a side panel and the workspace.
 * `direction` says which way the pointer moves to grow the adjacent panel:
 * a left panel grows as the pointer moves right (+x); a right panel grows as
 * the pointer moves left (-x). Keyboard arrows nudge the width for a11y. */
import { type KeyboardEvent, type PointerEvent, useCallback } from "react";

export function ResizeHandle({
  label,
  direction,
  width,
  onResize,
}: {
  label: string;
  direction: "left" | "right";
  width: number;
  onResize: (next: number) => void;
}) {
  const sign = direction === "left" ? 1 : -1;

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = width;
      const move = (ev: globalThis.PointerEvent) => onResize(startW + sign * (ev.clientX - startX));
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        document.body.classList.remove("resizing");
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      document.body.classList.add("resizing");
    },
    [width, sign, onResize],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      const step = (e.shiftKey ? 32 : 8) * sign;
      if (e.key === "ArrowLeft") onResize(width - step);
      else if (e.key === "ArrowRight") onResize(width + step);
    },
    [width, sign, onResize],
  );

  return (
    <div
      className="resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}
