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

/** Kept in step with `.toast-viewport`'s width in `components.css`, because the
 * placement has to know how wide the card is to keep it on screen. */
const VIEWPORT_WIDTH = 360;

type Anchor = { top: number; left: number };

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
  if (!document.contains(activation.el)) return null;

  const rect = activation.el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;

  // Beside it where there is room, otherwise on its other side, and never off
  // the edge: a card that is half outside the window says less than one in the
  // corner.
  let left = rect.right + ANCHOR_GAP;
  if (left + VIEWPORT_WIDTH > window.innerWidth) {
    left = rect.left - VIEWPORT_WIDTH - ANCHOR_GAP;
  }
  left = Math.max(ANCHOR_GAP, Math.min(left, window.innerWidth - VIEWPORT_WIDTH - ANCHOR_GAP));
  const top = Math.max(ANCHOR_GAP, Math.min(rect.top, window.innerHeight - 120));
  return { top, left };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
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
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, []);

  const push = useCallback((msg: ToastInput) => {
    // Resolved at push time rather than at render: the control may be gone by
    // the time the card is drawn — a dialog that closed on the same click — and
    // the place it was is still where the reader was looking.
    setAnchor(anchorFor(activation.current));
    const item: ToastItem = { id: nextId++, variant: "info", ...msg };
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
        {items.map((t) => (
          <ToastPrimitive.Root
            key={t.id}
            className={`toast toast-${t.variant}`}
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
        <ToastPrimitive.Viewport
          className="toast-viewport"
          // `right: auto` because the stylesheet pins it to the right edge, and
          // an inline `left` alone would leave both set and the width fighting.
          style={anchor ? { top: anchor.top, left: anchor.left, right: "auto" } : undefined}
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
