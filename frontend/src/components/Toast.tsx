/** Toast notifications on Radix Toast. useToast() pushes messages.
 *
 * **Where a message appears is part of what it says.** These used to land in the
 * far top-right corner, which on a wide monitor is a long way from the button
 * that caused them — far enough to be missed entirely. A message about
 * something you just did belongs beside the thing you did it with.
 *
 * No call site says what to anchor to, because the browser already knows: the
 * element that was activated is recorded here, and a toast pushed shortly after
 * is placed beside it. Everything else — a generation finishing on an SSE
 * event, a print ending an hour after the dialog closed — has no trigger to
 * point at and keeps the corner it always had. That fallback is the whole
 * reason this is not "anchor everything": a message with nowhere to anchor must
 * not be a message that disappears.
 *
 * **The place belongs to the message, not to the viewport.** Errors do not time
 * out, so two toasts from two different controls are the ordinary state rather
 * than a corner case — and a shared position would mean the second one dragged
 * the first to a control it had nothing to do with, which is the exact inverse
 * of what this is for. An anchored toast is therefore taken out of the
 * viewport's flow and placed itself; the un-anchored ones stay in it and go on
 * stacking in the corner exactly as they did.
 */
import * as ToastPrimitive from "@radix-ui/react-toast";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { CloseIcon } from "./icons";
import { IconButton } from "./primitives";

export type ToastVariant = "info" | "success" | "error";

/** A toast to push. `duration` overrides the provider default for this one
 * toast and `Infinity` holds it open until dismissed. Radix resolves the value
 * as `duration || providerDefault`, so 0 reads as "use the default" rather than
 * "never show", and a negative value closes on the next tick. Errors already
 * stay open on their own, so callers rarely need to set this at all. */
interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
}

interface ToastItem extends ToastInput {
  id: number;
  variant: ToastVariant;
  /** Where this one belongs, decided when it was pushed and never revisited.
   * `null` is the corner, and is what most messages are. */
  anchor: Anchor | null;
}

interface ToastApi {
  toast: (msg: ToastInput) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

let nextId = 1;

/** Errors do not time out any more, and they arrive one per failed action — a
 * panel that reloads on every open contributes one each time. Without a ceiling
 * the stack creeps back over the controls the viewport was moved to clear, so
 * keep only the newest few. */
const MAX_TOASTS = 4;

/** How long after an activation a toast still counts as belonging to it.
 *
 * Long enough for a round trip to the local server, which is what saving,
 * testing and forgetting are. Deliberately *not* long enough for slicing or
 * printing: those take minutes to hours, by which time the reader has looked
 * away, and a bubble beside a button nobody is watching is worse than a message
 * where messages go.
 */
const ANCHOR_WINDOW_MS = 3000;

/** Breathing room between the control and the card, in pixels. */
const ANCHOR_GAP = 8;

/** How wide a card is, in pixels.
 *
 * The stylesheet reads it from `--toast-width`, which this component writes onto
 * the viewport — so the placement arithmetic and the rendered box cannot drift.
 * A comment claiming the two agree would have gone stale silently the first time
 * somebody edited the CSS.
 */
const TOAST_WIDTH = 360;

/** How far down the next card sits when two share an anchor.
 *
 * An approximation, and it only has to be one: cards at the same anchor need two
 * messages from one control inside {@link ANCHOR_WINDOW_MS}, which is rare, and
 * they are all within a line of each other in height. The corner stack does not
 * use this at all — those are still laid out by the viewport's own flex column.
 */
const ANCHORED_STACK_STEP = 96;

/** How much room to leave below an anchored card so it cannot be placed with its
 * dismiss button off the bottom of the window. */
const ANCHOR_BOTTOM_ROOM = 120;

type Anchor = { top: number; left: number };

/** How many earlier toasts share this one's place.
 *
 * Only ever more than zero for two messages from one control inside the anchor
 * window; the corner stack is laid out by the viewport and never comes here.
 */
function sameAnchorBefore(items: ToastItem[], index: number): number {
  const mine = items[index].anchor;
  if (!mine) return 0;
  return items
    .slice(0, index)
    .filter((t) => t.anchor?.top === mine.top && t.anchor?.left === mine.left).length;
}

/** Where to put the viewport for a toast caused by `activation`, or `null` to
 * leave it where the stylesheet puts it.
 *
 * Returns `null` rather than guessing whenever the claim would be weak: too
 * long ago, the element gone from the document, or a rect of nothing — which is
 * what a detached or `display: none` element measures, and what jsdom returns
 * for everything.
 */
function anchorFor(activation: { el: Element; at: number } | null): Anchor | null {
  if (!activation) return null;
  if (Date.now() - activation.at > ANCHOR_WINDOW_MS) return null;
  // Detached: the caller clears the record on this answer, so a control taken
  // off the page with its subtree is not held here until the next click.
  if (!document.contains(activation.el)) return null;

  const rect = activation.el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;

  // Beside it where there is room, otherwise on its other side, and never off
  // the edge: a card that is half outside the window says less than one in the
  // corner.
  let left = rect.right + ANCHOR_GAP;
  if (left + TOAST_WIDTH > window.innerWidth) {
    left = rect.left - TOAST_WIDTH - ANCHOR_GAP;
  }
  left = Math.max(ANCHOR_GAP, Math.min(left, window.innerWidth - TOAST_WIDTH - ANCHOR_GAP));
  const top = Math.max(
    ANCHOR_GAP,
    Math.min(rect.top, window.innerHeight - ANCHOR_BOTTOM_ROOM),
  );
  return { top, left };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const activation = useRef<{ el: Element; at: number } | null>(null);

  useEffect(() => {
    // Capture phase, so the element is recorded *before* the handler that will
    // push the toast runs. `click` rather than `pointerdown` because it fires
    // for a keyboard activation too, and somebody driving this from the
    // keyboard has the same claim to being told where they are looking.
    const onClick = (event: MouseEvent) => {
      const target = event.target;
      const el =
        target instanceof Element
          ? target.closest("button, a, summary, [role='button']")
          : null;
      activation.current = el ? { el, at: Date.now() } : null;
    };
    // A placement is a set of window coordinates, and resizing invalidates every
    // one of them at once: an error does not time out, so a card placed against
    // the old width would otherwise sit off the edge for as long as it is open.
    // Sending them back to the corner is the answer that is never wrong.
    const onResize = () => {
      activation.current = null;
      setItems((prev) =>
        prev.every((t) => t.anchor === null) ? prev : prev.map((t) => ({ ...t, anchor: null })),
      );
    };

    document.addEventListener("click", onClick, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const push = useCallback((msg: ToastInput) => {
    // Resolved at push time rather than at render, and carried on the message
    // rather than shared: the control may be gone by the time the card is drawn
    // — a dialog that closed on the same click — and the place it was is still
    // where the reader was looking.
    const item: ToastItem = {
      id: nextId++,
      variant: "info",
      ...msg,
      anchor: anchorFor(activation.current),
    };
    // Nothing else will clear it, and the element it names may already be
    // detached — keeping it would pin that subtree until somebody clicked again.
    if (!item.anchor) activation.current = null;
    // Staying put belongs to the severity, not to whichever helper was called:
    // an error pushed through the generic api must not quietly time out.
    if (item.duration === undefined && item.variant === "error") {
      item.duration = Infinity;
    }
    setItems((prev) => [...prev, item].slice(-MAX_TOASTS));
  }, []);

  // Memoised: pushing a toast re-renders this provider, so an unstable value
  // would re-run every consumer effect that depends on it — and an effect that
  // pushes on failure would then push again, forever.
  const api: ToastApi = useMemo(
    () => ({
      toast: push,
      success: (title, description) => push({ title, description, variant: "success" }),
      // An error that times out mid-read is gone for good — there is no history
      // to go back to. push() holds every error open now that it can be closed.
      error: (title, description) => push({ title, description, variant: "error" }),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      <ToastPrimitive.Provider swipeDirection="right" duration={4000}>
        {children}
        {items.map((t, index) => (
          <ToastPrimitive.Root
            key={t.id}
            className={`toast toast-${t.variant}${t.anchor ? " toast-anchored" : ""}`}
            // Anchored cards leave the viewport's flex column and place
            // themselves; the rest stay in it and stack in the corner as before.
            // The offset only matters for the rare pair that share an anchor.
            style={
              t.anchor
                ? {
                    top: t.anchor.top + sameAnchorBefore(items, index) * ANCHORED_STACK_STEP,
                    left: t.anchor.left,
                  }
                : undefined
            }
            // Undefined falls back to the provider's duration; Infinity means
            // no timer at all, so the toast waits for the close button.
            duration={t.duration}
            onOpenChange={(open) => {
              if (!open) setItems((prev) => prev.filter((x) => x.id !== t.id));
            }}
          >
            <ToastPrimitive.Title className="toast-title">{t.title}</ToastPrimitive.Title>
            {t.description && (
              <ToastPrimitive.Description className="toast-desc">
                {t.description}
              </ToastPrimitive.Description>
            )}
            {/* Swiping right closes a toast too, but nothing on screen says so. */}
            <ToastPrimitive.Close asChild>
              <IconButton className="toast-close" label={`Dismiss ${t.title}`}>
                <CloseIcon />
              </IconButton>
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        {/* The width lives here rather than in two places: the stylesheet reads
            it back out, so the arithmetic above and the rendered box are the
            same number by construction. */}
        <ToastPrimitive.Viewport
          className="toast-viewport"
          style={{ "--toast-width": `${TOAST_WIDTH}px` } as React.CSSProperties}
        />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
