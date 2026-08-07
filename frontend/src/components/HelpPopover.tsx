/** The explanation a control is worth having, without spending a paragraph on
 * it every time the panel is looked at.
 *
 * It floats over what is underneath rather than pushing it down: a note that
 * moves the buttons while it is being read costs more than it explains. Built
 * on Radix Popover rather than the tooltip next door, because this is a card
 * someone can point at and read at their own pace — a tooltip is a label that
 * follows a mouse, and it has no answer for a finger on a screen.
 *
 * A card the mouse merely opened goes away when the mouse leaves, and there is
 * no bridge across the gap to it: it is there to be read in place. Click the
 * mark to keep it, and then it can be reached into.
 */
import * as Popover from "@radix-ui/react-popover";
import { type ReactNode, useId, useRef, useState } from "react";

import { InfoIcon } from "./icons";

export function HelpPopover({
  label,
  title,
  children,
}: {
  /** How the mark is named to anyone who cannot see it. */
  label: string;
  title: string;
  children: ReactNode;
}) {
  // Two ways of being open, kept apart on purpose. A mouse that happened to
  // pass over should give the note up again when it leaves; a click is someone
  // asking for it, and that one stays until it is dismissed.
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const mark = useRef<HTMLButtonElement>(null);
  // Whether the card was given the focus when it opened. Only then is there
  // something to give back.
  const tookFocus = useRef(false);
  const cardId = useId();
  const titleId = useId();
  const open = pinned || hovering;

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        // Radix only reports its own dismissals here — Escape, a click
        // elsewhere. Either settles both ways of being open.
        if (!next) {
          setPinned(false);
          setHovering(false);
        }
      }}
    >
      {/* An anchor rather than a trigger: Radix's trigger toggles on the open
          state it can see, so a card the mouse had already opened would close
          on the very click that meant to keep it. The trigger's own wiring —
          the description of what the mark opens, and the focus handed back
          when it closes — is spelled out here instead. */}
      <Popover.Anchor asChild>
        <button
          ref={mark}
          type="button"
          className="help-dot"
          aria-label={label}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-controls={open ? cardId : undefined}
          onClick={() => {
            setHovering(false);
            setPinned((was) => !was);
          }}
          // Anything but a finger. A touch has no hover to speak of — it lands
          // as a tap, which is the click above, and treating it as a hover
          // would open the card and leave it with nothing to close it.
          onPointerEnter={(e) => e.pointerType !== "touch" && setHovering(true)}
          onPointerLeave={(e) => e.pointerType !== "touch" && setHovering(false)}
        >
          ?
        </button>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          id={cardId}
          className="help-card"
          side="top"
          sideOffset={6}
          collisionPadding={8}
          aria-labelledby={titleId}
          // Escape belongs to the thing on top. The panel this opens inside is
          // listening for it on the document, and without this one press would
          // put the card away and take the panel with it. Radix listens in the
          // capture phase, so stopping it here is enough — and it is stopping,
          // not preventing: a prevented default reads as "handled, stay open".
          onEscapeKeyDown={(e) => e.stopPropagation()}
          // Opened by a passing mouse, the card is read where it stands;
          // pulling the focus into it would strand a keyboard somewhere nobody
          // asked to go. Asked for by a click, it is somewhere to go.
          onOpenAutoFocus={(e) => {
            tookFocus.current = pinned;
            if (!pinned) e.preventDefault();
          }}
          // And where the focus came from is where it goes back to. Radix would
          // return it to a trigger, and there is no trigger here to return it
          // to, so it would land on the document and the next Tab would start
          // over at the top of the page.
          onCloseAutoFocus={() => {
            if (!tookFocus.current) return;
            tookFocus.current = false;
            mark.current?.focus();
          }}
          // The mark is outside the card, so a click on it reads as a dismissal
          // and would undo the toggle above before it happened.
          onPointerDownOutside={(e) => {
            if (mark.current?.contains(e.target as Node)) e.preventDefault();
          }}
        >
          <p className="help-card-head" id={titleId}>
            <InfoIcon size={14} />
            {title}
          </p>
          <div className="help-card-body">{children}</div>
          <Popover.Arrow className="help-arrow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
