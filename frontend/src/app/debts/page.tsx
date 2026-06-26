"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Search, RefreshCw, ArrowUpRight } from "lucide-react";

interface ClientDebt {
  client_id: number;
  client_name: string;
  client_phone: string;
  debt_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}

function paymentState(row: ClientDebt) {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: "warning" as const };
  }
  if (row.partial_count > 0) {
    return { label: "Частично оплачен", tone: "warning" as const };
  }
  return { label: "Не оплачен", tone: "destructive" as const };
}

function DebtsPageInner() {
  const { data, loading, reload } = useApi<ClientDebt[]>("/clients/debts/");
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const rows = data ?? [];
  const totalDebt = rows.reduce((sum, row) => sum + Number(row.debt_total), 0);
  const totalOrders = rows.reduce((sum, row) => sum + row.orders_count, 0);
  const partialClients = rows.filter((row) => row.partial_count > 0).length;
  const overdueClients = rows.filter((row) => row.overdue_count > 0).length;

  const filtered = rows.filter((row) =>
    !q || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(q.toLowerCase())
  );

  async function checkOverdue() {
    setBusy(true); setCheckMsg("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setCheckMsg(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      await reload();
    } catch (e) {
      setCheckMsg(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell title="Долги" section="Обзор" description="Общий долг клиента с переходом к заказам внутри."
      actions={
        <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue}>
          <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
          <span className="hidden sm:inline">Проверить просрочки</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <StatCard label="Клиентов с долгом" value={String(rows.length)} />
        <StatCard label="Общий остаток" value={`${formatMoney(String(totalDebt))} ₸`} accent />
        <StatCard label="Заказов в долге" value={String(totalOrders)} />
        <StatCard label="Частично оплачено" value={String(partialClients)} caption={`Просрочек: ${overdueClients}`} />
      </section>

      {checkMsg && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {checkMsg}
        </p>
      )}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Клиенты</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            На этом уровне показывается общий остаток. Заказы открываются внутри клиента.
          </p>
        </div>
        <div className="relative w-full sm:max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту или телефону"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Клиент</TH>
                <TH>Остаток</TH>
                <TH>Заказы</TH>
                <TH>Статус оплаты</TH>
                <TH>Магазины</TH>
                <TH>Просрочки</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
              ) : filtered.length === 0 ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Долгов нет.</TD></TR>
              ) : filtered.map((row) => {
                const state = paymentState(row);
                return (
                  <TR key={row.client_id}>
                    <TD>
                      <div className="font-medium">{row.client_name || "—"}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                    </TD>
                    <TD className="tabular-nums text-lg font-semibold text-[var(--destructive)]">
                      {formatMoney(row.debt_total)} ₸
                    </TD>
                    <TD className="tabular-nums">{row.orders_count}</TD>
                    <TD>
                      <Badge tone={state.tone} dot>{state.label}</Badge>
                    </TD>
                    <TD>
                      {row.stores_count > 0 ? (
                        <Badge tone="muted">{row.stores_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">—</span>
                      )}
                    </TD>
                    <TD>
                      {row.overdue_count > 0 ? (
                        <Badge tone="destructive" dot>{row.overdue_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">0</span>
                      )}
                    </TD>
                    <TD>
                      <div className="flex justify-end">
                        <Link href={`/debts/clients/${row.client_id}`}>
                          <Button size="sm" variant="ghost">
                            Детали
                            <ArrowUpRight className="size-4" />
                          </Button>
                        </Link>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function DebtsPage() {
  return <RequirePerm perm="reports.view" title="Долги"><DebtsPageInner /></RequirePerm>;
}
