"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { Search, RefreshCw } from "lucide-react";
import type { Order } from "@/lib/types";

export default function DebtsPage() {
  const { data: orders, loading, reload } = useApi<Order[]>("/orders/debts/");
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const list = orders ?? [];
  const remainingOf = (o: Order) =>
    Number(o.remaining_amount ?? (Number(o.total_amount) - Number(o.paid_total)));
  const totalDebt = list.reduce((s, o) => s + remainingOf(o), 0);
  const partialCount = list.filter((o) => o.payment_status === "partial").length;

  const filtered = list.filter((o) =>
    !q || `${o.client_name ?? ""} ${o.id} ${o.truck_number ?? ""}`.toLowerCase().includes(q.toLowerCase()));

  async function checkOverdue() {
    setBusy(true); setCheckMsg("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setCheckMsg(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      reload();
    } catch (e) { setCheckMsg(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Долги" section="Обзор" description="Отгруженные заказы с непогашенным долгом."
      actions={
        <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue}>
          <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
          <span className="hidden sm:inline">Проверить просрочки</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Заказов в долге" value={String(list.length)} />
        <StatCard label="Сумма долга" value={`${formatMoney(String(totalDebt))} ₸`} />
        <StatCard label="Частично оплачено" value={String(partialCount)} />
      </section>

      {checkMsg && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {checkMsg}
        </p>
      )}

      <div className="mb-4 relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input className="pl-9" placeholder="Поиск по клиенту, № заказа, КАМАЗу"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Заказ</TH><TH>Клиент</TH><TH>Сумма</TH><TH>Оплачено</TH>
                <TH>Остаток</TH><TH>Статус</TH><TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading ? (
                <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
              ) : filtered.length === 0 ? (
                <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Долгов нет.</TD></TR>
              ) : filtered.map((o) => (
                <TR key={o.id}>
                  <TD className="font-medium">#{o.id}
                    {o.truck_number && <span className="block text-xs text-[var(--muted-foreground)] tabular-nums">{o.truck_number}</span>}
                  </TD>
                  <TD>{o.client_name || "—"}</TD>
                  <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                  <TD className="tabular-nums text-[var(--success)]">{formatMoney(o.paid_total)} ₸</TD>
                  <TD className="tabular-nums font-medium text-[var(--destructive)]">
                    {formatMoney(String(remainingOf(o)))} ₸
                  </TD>
                  <TD>
                    <Badge tone={PAYMENT_STATUS_TONE[o.payment_status ?? "unpaid"] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[o.payment_status ?? "unpaid"] ?? o.payment_status}
                    </Badge>
                  </TD>
                  <TD>
                    <div className="flex justify-end">
                      <Link href={`/orders/${o.id}`}>
                        <Button size="sm" variant="ghost">Открыть</Button>
                      </Link>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
