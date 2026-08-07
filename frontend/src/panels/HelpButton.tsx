/** Keyboard-shortcut cheatsheet. Opens via the ? button or "?" key. */
import { useEffect, useState } from "react";

import { HelpIcon, IconButton, Modal, Tooltip } from "../components";

const SHORTCUTS: { keys: string; action: string }[] = [
  { keys: "⌘/Ctrl + Enter", action: "Generate / Refine from the prompt" },
  { keys: "?", action: "Open this shortcuts panel" },
  { keys: "Esc", action: "Close dialogs" },
  { keys: "Drag", action: "Orbit · right-drag to pan · scroll to zoom" },
];

export function HelpButton() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
      if (e.key === "?" && !typing) {
        e.preventDefault();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <Tooltip label="Keyboard shortcuts (?)">
        <IconButton label="Keyboard shortcuts" onClick={() => setOpen(true)}>
          <HelpIcon />
        </IconButton>
      </Tooltip>
      <Modal open={open} onOpenChange={setOpen} title="Keyboard shortcuts">
        <dl className="shortcuts">
          {SHORTCUTS.map((s) => (
            <div className="shortcut-row" key={s.keys}>
              <dt>
                <kbd>{s.keys}</kbd>
              </dt>
              <dd>{s.action}</dd>
            </div>
          ))}
        </dl>
      </Modal>
    </>
  );
}
