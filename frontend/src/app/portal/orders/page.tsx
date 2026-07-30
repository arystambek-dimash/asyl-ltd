"use client";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { DataGate } from "@/components/ui/data-state";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { formatPortalMoney } from "@/lib/utils";
import { Plus } from "lucide-react";
import type { PortalOrder } from "@/lib/types";

export default function PortalOrdersPage() {
  const { data: orders, loading, error, reload } = useApi<PortalOrder[]>("/portal/orders/");
  return (
    <AppShell
      title="Мои заказы"
      portal
      actions={
        <Link href="/portal/orders/new" className={buttonVariants({ size: "sm" })} aria-label="Новый заказ">
          <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
        </Link>
      }
    >
      <div className="mb-4">
        <p className="text-sm text-[var(--muted-foreground)]">{orders?.length ?? 0} заказов</p>
      </div>
      <Card>
        <CardContent className="pt-6">
          {!orders ? (
            <DataGate loading={loading} error={error} onRetry={reload} />
          ) : orders.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">У вас пока нет заказов.</p>
          ) : (
            <>
              <div className="space-y-3 md:hidden">
                {orders.map((order) => (
                  <Link
                    key={order.id}
                    href={`/portal/orders/${order.id}`}
                    className="block rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card transition hover:border-[var(--ring)]/30 hover:shadow-float focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40"
                    aria-label={`Открыть заказ №${order.id}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">
                          Заказ
                        </p>
                        <h3 className="mt-1 text-lg font-semibold tabular-nums">#{order.id}</h3>
                      </div>
                      <StatusBadge status={order.status} />
                    </div>
                    <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-4 text-sm">
                      <div>
                        <dt className="text-xs text-[var(--muted-foreground)]">Сумма</dt>
                        <dd
                          className={`mt-1 font-semibold ${
                            order.total_amount == null ? "text-[var(--muted-foreground)]" : "tabular-nums"
                          }`}
                        >
                          {formatPortalMoney(order.total_amount, order.currency)}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-[var(--muted-foreground)]">Оплачено</dt>
                        <dd className="mt-1 font-medium tabular-nums text-[var(--muted-foreground)]">
                          {formatPortalMoney(order.paid_total, order.currency)}
                        </dd>
                      </div>
                    </dl>
                  </Link>
                ))}
              </div>

              <div className="hidden md:block">
                <Table>
                  <THead>
                    <TR>
                      <TH>№</TH>
                      <TH>Сумма</TH>
                      <TH>Оплачено</TH>
                      <TH>Статус</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {orders.map((o) => (
                      <TR key={o.id}>
                        <TD className="font-medium">
                          <Link
                            href={`/portal/orders/${o.id}`}
                            className="rounded underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40"
                          >
                            #{o.id}
                          </Link>
                        </TD>
                        <TD className={o.total_amount == null ? "text-[var(--muted-foreground)]" : "tabular-nums"}>
                          {formatPortalMoney(o.total_amount, o.currency)}
                        </TD>
                        <TD
                          className={
                            o.paid_total == null
                              ? "text-[var(--muted-foreground)]"
                              : "tabular-nums text-[var(--muted-foreground)]"
                          }
                        >
                          {formatPortalMoney(o.paid_total, o.currency)}
                        </TD>
                        <TD>
                          <StatusBadge status={o.status} />
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
