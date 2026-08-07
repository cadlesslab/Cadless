/** Accessible dialogs — replaces browser prompt()/confirm().
 * Built on Radix Dialog (focus trap, Esc, aria roles for free). */
import * as Dialog from "@radix-ui/react-dialog";
import { type FormEvent, type ReactNode, useState } from "react";

import { Button, TextInput } from "./primitives";

export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className="modal-content"
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <Dialog.Title className="modal-title">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="modal-desc">{description}</Dialog.Description>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Single-line text prompt modal (create/rename). */
export function PromptDialog({
  open,
  title,
  label,
  initialValue = "",
  confirmLabel = "Save",
  onSubmit,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  label: string;
  initialValue?: string;
  confirmLabel?: string;
  onSubmit: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  // re-seed when reopened with a different initial value
  const [seen, setSeen] = useState(initialValue);
  if (open && seen !== initialValue) {
    setSeen(initialValue);
    setValue(initialValue);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const v = value.trim();
    if (v) onSubmit(v);
  }

  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title={title}>
      <form onSubmit={submit} className="modal-form">
        <label className="modal-label">
          {label}
          <TextInput
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label={label}
          />
        </label>
        <div className="modal-footer">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!value.trim()}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** Yes/No confirmation modal (delete). */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Delete",
  destructive = true,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  message: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onOpenChange={(o) => !o && onClose()} title={title} description={message}>
      <div className="modal-footer">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="button"
          variant={destructive ? "danger" : "primary"}
          onClick={onConfirm}
        >
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
