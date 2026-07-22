"use client";
import { Modal } from "./modal";
import { Button } from "./button";

export function ConfirmDialog({
  open,
  onClose,
  title,
  description,
  confirmLabel = "Удалить",
  confirmVariant = "destructive",
  busy,
  error,
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
          <Button type="button" variant={confirmVariant} onClick={onConfirm} disabled={busy}>
            {busy ? "Выполнение…" : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {description && <p className="text-sm text-[var(--muted-foreground)]">{description}</p>}
        {error && (
          <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
