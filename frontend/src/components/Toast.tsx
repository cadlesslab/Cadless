/** Toast notifications on Radix Toast. useToast() pushes messages. */
import * as ToastPrimitive from "@radix-ui/react-toast";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((msg: ToastInput) => {
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
        <ToastPrimitive.Viewport className="toast-viewport" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}
