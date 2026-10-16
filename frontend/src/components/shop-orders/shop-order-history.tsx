"use client";
import { Undo2 } from "lucide-react";
import { RECEIVE_METHOD_OPTIONS } from "@/components/payments/order-payment-actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { LoadMore } from "@/components/ui/load-more";
import { EmptyRow, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import type { ShopOrder } from "@/lib/types";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { usePagedApi } from "@/lib/use-paged-api";
import { formatCurrency, formatDateTime } from "@/lib/utils";

export function ShopOrderHistory({
  orders,
  onChanged,
}: {
  orders: ReturnType<typeof usePagedApi<ShopOrder>>;
  onChanged: () => void;
}) {
  const { me } = useAuth();
  const cancel = useConfirmAction<ShopOrder>(async (order) => {
    await api.post(`/shop-orders/${order.id}/cancel/`);
    onChanged();
  });
  return (
    <>
      <Card>
        <Table>
          <THead>
            <TR>
              <TH>Заказ</TH>
              <TH>Покупатель / товары</TH>
              <TH>Статус</TH>
              <TH className="text-right">Сумма / оплата</TH>
              <TH>
                <span className="sr-only">Действия</span>
              </TH>
            </TR>
          </THead>
          <TBody>
            {orders.loading ? (
              <EmptyRow colSpan={5}>Загрузка…</EmptyRow>
            ) : (
              !orders.items.length && <EmptyRow colSpan={5}>Заказов пока нет</EmptyRow>
            )}
            {orders.items.map((order) => (
              <TR key={order.id}>
                <TD className="align-top py-3">
                  <div className="font-medium">№{order.id}</div>
                  <div className="text-xs text-[var(--muted-foreground)] whitespace-nowrap">
                    {formatDateTime(order.sold_at ?? order.created_at)}
                  </div>
                </TD>
                <TD className="min-w-44 py-3">
                  <div className="font-medium">{order.client_name || "Розничный покупатель"}</div>
                  {order.items.map((item) => (
                    <div key={item.id} className="text-xs text-[var(--muted-foreground)]">
                      {item.product_label} × {item.quantity}
                    </div>
                  ))}
                </TD>
                <TD>
                  <Badge tone={order.status === "completed" ? "success" : "muted"}>{order.status_label}</Badge>
                </TD>
                <TD className="text-right whitespace-nowrap tabular-nums">
                  <div>{formatCurrency(order.total_amount, order.currency)}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Оплачено: {formatCurrency(order.paid_total, order.currency)}
                  </div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Остаток: {formatCurrency(order.remaining, order.currency)}
                  </div>
                  {order.payment && (
                    <div className="text-xs text-[var(--muted-foreground)]">
                      {RECEIVE_METHOD_OPTIONS.find(({ key }) => key === order.payment?.method)?.label}
                      {order.payment.voided_at ? " · Оплата отменена" : ""}
                    </div>
                  )}
                </TD>
                <TD className="text-right">
                  {order.status === "completed" && (Number(order.paid_total) <= 0 || can(me, "payments.create")) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Отменить заказ №${order.id}`}
                      onClick={() => cancel.open(order)}
                    >
                      <Undo2 className="size-4" />
                      <span className="hidden sm:inline">Отменить</span>
                    </Button>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </Card>
      <LoadMore
        shown={orders.items.length}
        total={orders.count}
        hasMore={orders.hasMore}
        loading={orders.loadingMore}
        onClick={orders.loadMore}
      />
      <ConfirmDialog
        {...cancel.dialog}
        title={cancel.item ? `Отменить заказ магазина №${cancel.item.id}?` : "Отменить заказ?"}
        description={`${cancel.item?.stock_deducted === false ? "Этот заказ оформлен задним числом без списания: остаток магазина не изменится." : "Товар вернётся только на остаток магазина."} Основной склад мельницы не изменится.${cancel.item?.payment && !cancel.item.payment.voided_at ? " Оплата магазина будет отменена." : ""}`}
        confirmLabel="Отменить заказ"
      />
    </>
  );
}
