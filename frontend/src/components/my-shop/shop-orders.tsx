"use client";
import { RotateCcw, ScrollText, Undo2 } from "lucide-react";
import { OrderPaymentBadge } from "@/components/payments/order-payment-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { LoadMore } from "@/components/ui/load-more";
import { api } from "@/lib/api";
import { groupByDay } from "@/lib/day-groups";
import type { MyShopOrder } from "@/lib/types";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { useLocalDay } from "@/lib/use-local-day";
import { bagsLabel, formatCurrency, formatTime } from "@/lib/utils";

/** «Мои заказы»: что и когда списано, сколько должен магазин; «Повторить» и отмена в течение часа. */
export function ShopOrders({
  orders,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
  onRepeat,
  onChanged,
}: {
  orders: MyShopOrder[];
  total: number;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRepeat: (order: MyShopOrder) => void;
  onChanged: () => void;
}) {
  const currentDay = useLocalDay();
  const cancel = useConfirmAction<MyShopOrder>(async (order) => {
    await api.post(`/my-shop/orders/${order.id}/cancel/`);
    onChanged();
  });

  if (orders.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 py-14 text-center">
          <ScrollText className="size-8 text-[var(--muted-foreground)]" />
          <div className="text-sm font-medium">Заказов пока нет</div>
          <p className="max-w-xs text-xs text-[var(--muted-foreground)]">
            Выберите товары во вкладке «Заказать» — здесь появится, что и когда списано со склада.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {groupByDay(orders, (order) => new Date(order.created_at), currentDay).map((group) => (
        <section key={group.key}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
            {group.label}
          </h3>
          <Card>
            <ul className="divide-y">
              {group.items.map((order) => (
                <ShopOrderRow
                  key={order.id}
                  order={order}
                  onRepeat={() => onRepeat(order)}
                  onCancel={() => cancel.open(order)}
                />
              ))}
            </ul>
          </Card>
        </section>
      ))}
      <LoadMore shown={orders.length} total={total} hasMore={hasMore} loading={loadingMore} onClick={onLoadMore} />

      <ConfirmDialog
        {...cancel.dialog}
        title={cancel.item ? `Отменить заказ №${cancel.item.id}?` : "Отменить заказ?"}
        description="Мешки вернутся на склад, а сумма заказа уйдёт из долга магазина."
        confirmLabel="Отменить заказ"
      />
    </div>
  );
}

function ShopOrderRow({
  order,
  onRepeat,
  onCancel,
}: {
  order: MyShopOrder;
  onRepeat: () => void;
  onCancel: () => void;
}) {
  const total = order.total_amount;
  const cancelled = total == null;
  // Долг строкой — только у частично оплаченного: у неоплаченного он равен сумме и бейджу «Не оплачен».
  const partialDebt = Number(order.paid_total) > 0 ? order.remaining_amount : null;
  const canCancel = order.status === "shipped" && order.cancel_blocker === "";
  return (
    <li className="flex flex-col gap-2.5 px-4 py-3 sm:px-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="font-semibold">№{order.id}</span>
            <span className="text-[var(--muted-foreground)] tabular-nums">{formatTime(order.created_at)}</span>
            <span className="text-[var(--muted-foreground)]">· {bagsLabel(order.bags)}</span>
            {cancelled ? <Badge tone="muted">{order.status_label}</Badge> : <OrderPaymentBadge order={order} />}
          </div>
          <ul className={cancelled ? "mt-1 text-xs text-[var(--muted-foreground)] line-through" : "mt-1 text-xs"}>
            {order.items.map((item) => (
              <li key={item.id} className="tabular-nums">
                {item.product_label} × {item.quantity}
                {item.is_bonus && " · бонус"}
              </li>
            ))}
          </ul>
        </div>
        {total != null && (
          <div className="shrink-0 text-right">
            <div className="whitespace-nowrap font-semibold tabular-nums">{formatCurrency(total, order.currency)}</div>
            {partialDebt && Number(partialDebt) > 0 && (
              <div className="whitespace-nowrap text-xs tabular-nums text-[var(--muted-foreground)]">
                долг {formatCurrency(partialDebt, order.currency)}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="outline" onClick={onRepeat}>
          <RotateCcw className="size-4" /> Повторить
        </Button>
        {canCancel && (
          <Button
            size="sm"
            variant="ghost"
            className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
            title="Отменить можно в течение часа после списания"
            onClick={onCancel}
          >
            <Undo2 className="size-4" /> Отменить
          </Button>
        )}
      </div>
    </li>
  );
}
