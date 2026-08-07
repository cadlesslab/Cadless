/** Fixed left activity rail + the floating panel it opens.
 *
 * The rail is always visible (icon-only); clicking an icon pops out a flyout
 * panel over the left edge of the viewport. Clicking the active icon again,
 * pressing Esc, or clicking outside closes it, so the viewport stays full-width
 * by default. Brand sits at the top; theme + help are pinned at the bottom. */
import { useEffect, useRef } from "react";

import {
  CadlessIcon,
  IconButton,
  MoonIcon,
  SunIcon,
  Tooltip,
} from "../components";
import { ResizeHandle } from "../components/ResizeHandle";
import { useStore, useStoreSelector } from "../state";
import { applyTheme } from "../theme/theme";
import { HelpButton } from "./HelpButton";
import { panelFor, type PanelId, registeredPanels } from "./registry";
// Side effect: the panels this tree ships hand themselves in. Imported here
// rather than at the app root so anything that renders the rail — a test
// included — sees the same set the app does.
import "./builtins";
// And, after them, whatever a composed build was given from outside the tree.
// Second so a plugin can replace a built-in rather than be replaced by one.
import "./plugins";

export type { PanelId } from "./registry";

export function LeftRail({
  active,
  onSelect,
}: {
  active: PanelId | null;
  onSelect: (id: PanelId) => void;
}) {
  const store = useStore();
  const theme = useStoreSelector((s) => s.theme);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    store.set({ theme: next });
  }

  return (
    <nav className="rail" aria-label="Workspace">
      <div className="rail-brand" aria-hidden>
        <CadlessIcon size={24} />
      </div>

      <div className="rail-items">
        {registeredPanels().map(({ id, entry }) => (
          <Tooltip key={id} label={entry.label} side="right">
            <button
              className={`rail-btn ${active === id ? "active" : ""}`}
              aria-label={entry.label}
              aria-pressed={active === id}
              onClick={() => onSelect(id)}
            >
              {entry.icon}
            </button>
          </Tooltip>
        ))}
      </div>

      <div className="rail-bottom">
        <HelpButton />
        <Tooltip label={theme === "dark" ? "Light theme" : "Dark theme"} side="right">
          <IconButton label="Toggle theme" onClick={toggleTheme}>
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </IconButton>
        </Tooltip>
      </div>
    </nav>
  );
}

/** Don't treat clicks inside the rail or inside portalled overlays
 * (dialogs/tooltips/toasts) as "outside" — those belong to the panel. */
function isOutside(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== "function") return true;
  return !el.closest(
    ".rail, .flyout, .modal-overlay, .modal-content, .toast-viewport, [data-radix-popper-content-wrapper]",
  );
}

export function RailFlyout({
  id,
  width,
  onResize,
  onClose,
}: {
  id: PanelId;
  width: number;
  onResize: (w: number) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    function onPointer(e: MouseEvent) {
      if (isOutside(e.target)) onClose();
    }
    // Escape belongs to whatever is on top, and what is on top says so: a help
    // card opened in here stops the key in the capture phase, so this never
    // hears the press that closed it. Nothing to check for here.
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <aside className="flyout" ref={ref} style={{ width: `${width}px` }} aria-label={`${id} panel`}>
      <div className="flyout-body">{panelFor(id)?.render()}</div>
      <ResizeHandle label="Resize panel" direction="left" width={width} onResize={onResize} />
    </aside>
  );
}
