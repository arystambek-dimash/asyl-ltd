import { Badge } from "@/components/ui/badge";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";

type PaymentBadgeOrder = {
  status: string;
  payment_status?: string;
  items?: { quantity: number; returned_quantity?: number }[];
};

/** Бейдж заказа, который клиент вернул целиком («Возврат»): платить не за что, это не долг. */
const RETURNED = "returned";

function fullyReturned(order: PaymentBadgeOrder): boolean {
  const items = order.items ?? [];
  return items.length > 0 && items.every((item) => (item.returned_quantity ?? 0) >= item.quantity);
}

/**
 * Статус оплаты для бейджа заказа; null — бейдж не нужен.
 *
 * У отгруженного заказа оплата — главный вопрос, её показываем всегда. До
 * отгрузки долга ещё нет, поэтому «Не оплачен» там только шумит; а вот
 * предоплату (частичную или полную) видно сразу — в карточке, списке и портале.
 */
export function paymentBadgeStatus(order: PaymentBadgeOrder): string | null {
  const status = order.payment_status;
  if (!status) return null;
  if (fullyReturned(order)) return RETURNED;
  if (order.status === "shipped") return status;
  return status === "partial" || status === "settled" ? status : null;
}

/** pending — у заказа есть оплата на проверке у кассы: она важнее статуса оплаты. */
export function OrderPaymentBadge({
  order,
  pending,
  dot,
}: {
  order: PaymentBadgeOrder;
  pending?: boolean;
  dot?: boolean;
}) {
  if (pending) {
    return (
      <Badge tone="warning" dot={dot}>
        На проверке
      </Badge>
    );
  }
  const status = paymentBadgeStatus(order);
  if (!status) return null;
  if (status === RETURNED) {
    return (
      <Badge tone="muted" dot={dot}>
        Возвращён
      </Badge>
    );
  }
  return (
    <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot={dot}>
      {PAYMENT_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
