"use client";
import { Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { LoadMore } from "@/components/ui/load-more";
import { EmptyRow, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
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
              <TH className="text-right">Сумма</TH>
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
                    {formatDateTime(order.created_at)}
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
                  {formatCurrency(order.total_amount, order.currency)}
                </TD>
                <TD className="text-right">
                  {order.status === "completed" && (
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
        description="Товар вернётся только на остаток магазина. Основной склад мельницы не изменится."
        confirmLabel="Отменить заказ"
      />
    </>
  );
}
