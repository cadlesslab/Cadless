/** Basic styled primitives. All colors come from theme tokens. */
import {
  forwardRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";

/** Join class names, leaving out the ones that are not there.
 *
 * `${a} ${b}` keeps the space even when one side is empty, and an empty
 * `className` is the ordinary case — which is why every control in this file
 * rendered a `class` attribute ending in a space. */
function cx(...parts: (string | false | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "default" | "primary" | "ghost" | "danger";
type Size = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", size = "md", className = "", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      /* Only the classes that carry a rule. `btn-default` and `btn-md` never
         had one — they name what `.btn` already is — so they said nothing while
         looking like they said something, which is worse than saying nothing. */
      className={cx(
        "btn",
        variant !== "default" && `btn-${variant}`,
        size !== "md" && `btn-${size}`,
        className,
      )}
      {...rest}
    />
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ label, className = "", children, ...rest }, ref) {
    return (
      <button
        ref={ref}
        aria-label={label}
        title={label}
        className={cx("icon-btn", className)}
        {...rest}
      >
        {children}
      </button>
    );
  },
);

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className = "", ...rest }, ref) {
    return <input ref={ref} className={cx("field", className)} {...rest} />;
  },
);

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className = "", ...rest }, ref) {
  return <textarea ref={ref} className={cx("field", className)} {...rest} />;
});

export function Panel({
  title,
  actions,
  children,
  className = "",
  ...rest
}: { title?: ReactNode; actions?: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  return (
    <section className={cx("panel", className)} {...rest}>
      {(title || actions) && (
        <header className="panel-head">
          {title && <h2 className="panel-title">{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
