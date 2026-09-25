"use client";
import { Modal } from "./modal";
import { Button } from "./button";
import { FormError } from "./data-state";

export function ConfirmDialog({
  open,
  onClose,
  title,
  description,
  confirmLabel = "Удалить",
  confirmVariant = "destructive",
  busy,
  error,
  confirmDisabled,
  children,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  confirmVariant?: "default" | "destructive";
  busy?: boolean;
  error?: string;
  confirmDisabled?: boolean;
  children?: React.ReactNode;
  onConfirm: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Подтверждение"
      title={title}
      className="max-w-md"
      footer={
        <>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button type="button" variant={confirmVariant} onClick={onConfirm} disabled={busy || confirmDisabled}>
            {busy ? "Выполнение…" : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {description && <p className="text-sm text-[var(--muted-foreground)]">{description}</p>}
        {children}
        <FormError message={error} />
      </div>
    </Modal>
  );
}
