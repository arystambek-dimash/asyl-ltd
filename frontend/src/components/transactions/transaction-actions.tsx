"use client";
import type { LucideIcon } from "lucide-react";
import { Download, ExternalLink, RotateCcw, Send, Undo2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Payment } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACTIVE_PROVIDER_STATUSES = new Set(["creating", "processing", "pending", "cancelling"]);

export type TransactionActionKey = "restore" | "issue" | "qr" | "receipt" | "refund" | "reject";

export interface TransactionAction {
  key: TransactionActionKey;
  /** Подпись в списке (телефон) и на широких экранах у outline-кнопок. */
  label: string;
  /** Подсказка/имя для иконки без текста — как в таблице сейчас. */
  title: string;
  icon: LucideIcon;
  variant: "outline" | "ghost";
  destructive?: boolean;
  disabled?: boolean;
  run: () => void;
}

export interface TransactionActionHandlers {
  busy: boolean;
  receipt: (payment: Payment) => unknown;
  issue: (payment: Payment) => unknown;
  openRefund: (payment: Payment) => void;
  openReject: (payment: Payment) => void;
  openRestore: (payment: Payment) => void;
}

/** Какие действия доступны по операции — единый источник для таблицы и шторки. */
export function transactionActions(
  row: Payment,
  t: TransactionActionHandlers,
  perms: { canConfirm: boolean; canCreate: boolean },
): TransactionAction[] {
  const actions: TransactionAction[] = [];
  if (perms.canConfirm && row.can_restore) {
    actions.push({
      key: "restore",
      label: "Восстановить",
      title: "Восстановить отклонённую операцию",
      icon: Undo2,
      variant: "outline",
      run: () => t.openRestore(row),
    });
  }
  if (perms.canCreate && row.can_issue) {
    actions.push({
      key: "issue",
      label: "Отправить",
      title: "Отправить счёт клиенту",
      icon: Send,
      variant: "outline",
      disabled: t.busy,
      run: () => void t.issue(row),
    });
  }
  const qrUrl =
    row.provider?.channel === "qr" && row.provider.qr_token_url && ACTIVE_PROVIDER_STATUSES.has(row.provider.status)
      ? row.provider.qr_token_url
      : null;
  if (qrUrl) {
    actions.push({
      key: "qr",
      label: "Открыть Kaspi QR",
      title: "Открыть активный Kaspi QR",
      icon: ExternalLink,
      variant: "ghost",
      run: () => window.open(qrUrl, "_blank", "noopener"),
    });
  }
  if (row.status === "confirmed") {
    actions.push({
      key: "receipt",
      label: "Скачать выписку",
      title: "Скачать выписку ASYL LTD",
      icon: Download,
      variant: "ghost",
      run: () => void t.receipt(row),
    });
  }
  if (perms.canConfirm && row.status === "confirmed" && Number(row.available_for_refund ?? 0) > 0) {
    actions.push({
      key: "refund",
      label: "Вернуть оплату",
      title: row.provider ? "Вернуть через ApiPay" : "Вернуть деньги из кассы",
      icon: RotateCcw,
      variant: "ghost",
      run: () => t.openRefund(row),
    });
  }
  if (perms.canConfirm && ["requested", "received"].includes(row.status) && row.confirmation_mode !== "automatic") {
    actions.push({
      key: "reject",
      label: "Отклонить платёж",
      title: "Отклонить платёж",
      icon: XCircle,
      variant: "ghost",
      destructive: true,
      run: () => t.openReject(row),
    });
  }
  return actions;
}

/** `icons` — ряд иконок в ячейке таблицы (как сейчас), `list` — столбик широких кнопок в шторке. */
export function TransactionActions({ actions, layout }: { actions: TransactionAction[]; layout: "icons" | "list" }) {
  if (actions.length === 0) return null;
  if (layout === "list") {
    return (
      <div className="flex flex-col gap-2">
        {actions.map((action) => (
          <Button
            key={action.key}
            variant="outline"
            className={cn("h-11 justify-start", action.destructive && "text-[var(--destructive)]")}
            disabled={action.disabled}
            title={action.title}
            onClick={action.run}
          >
            <action.icon className="size-4" /> {action.label}
          </Button>
        ))}
      </div>
    );
  }
  return (
    <div className="flex justify-end gap-1">
      {actions.map((action) => (
        <Button
          key={action.key}
          size="sm"
          variant={action.variant}
          className={action.destructive ? "text-[var(--destructive)]" : undefined}
          disabled={action.disabled}
          title={action.title}
          onClick={action.run}
        >
          <action.icon className="size-4" />
          {action.variant === "outline" && <span className="hidden xl:inline">{action.label}</span>}
        </Button>
      ))}
    </div>
  );
}
