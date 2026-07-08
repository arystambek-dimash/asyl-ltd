"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { deptLabel } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Banknote, HandCoins } from "lucide-react";
import type { PaymentQueueItem } from "@/lib/types";

function CashierInner() {
  const { me } = useAuth();
  const { data: queue, loading, reload } =
    useApi<PaymentQueueItem[]>("/orders/payments-queue/?stage=accountant_ok");
  const [dept, setDept] = useState("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const all = queue ?? [];
  const list = all.filter((p) => dept === "all" || p.department === dept);
  const total = list.reduce((s, p) => s + Number(p.amount), 0);
  const cashSum = list.filter((p) => p.method === "cash")
    .reduce((s, p) => s + Number(p.amount), 0);

  const pills = [
    { key: "all", label: "Все", count: all.length },
    { key: "main", label: deptLabel(me, "main"), count: all.filter((p) => p.department === "main").length },
    { key: "field", label: deptLabel(me, "field"), count: all.filter((p) => p.department === "field").length },
  ];

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reload(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <AppShell title="Касса" section="Работа"
      description="Финальное подтверждение поступления денег. Без подтверждения кассира оплата не считается полученной.">
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Ожидают кассу" value={String(list.length)} icon={HandCoins} />
        <StatCard label="Наличными" value={`${formatMoney(cashSum)} ₸`} icon={Banknote} />
        <StatCard label="Всего к подтверждению" value={`${formatMoney(total)} ₸`} accent />
      </section>

      <div className="mb-4">
        <FilterDropdown label="Отдел" options={pills} active={dept} onChange={setDept} />
      </div>

      {error && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
          {error}
        </p>
      )}

      {loading ? (
        <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {list.map((p) => (
            <div key={p.id} className="flex flex-col gap-3 rounded-xl border bg-[var(--card)] p-4 shadow-card">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-lg font-bold tabular-nums">{formatMoney(p.amount)} ₸</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    <Link href={`/orders/${p.order}`} className="hover:underline">Заказ #{p.order}</Link>
                    {" · "}{p.client_name}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Badge tone={p.department === "field" ? "primary" : "muted"}>
                    {deptLabel(me, p.department)}
                  </Badge>
                  <Badge tone="outline">
                    {p.method_label || PAYMENT_METHOD_LABELS[p.method] || p.method}
                  </Badge>
                </div>
              </div>
              <div className="text-[11px] text-[var(--muted-foreground)]">
                {p.received_by_name && <>Принял: {p.received_by_name} · </>}
                {p.accountant_by_name && <>Сверил: {p.accountant_by_name}</>}
                {p.accountant_at && <> · {new Date(p.accountant_at).toLocaleString("ru-RU")}</>}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button size="sm" disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${p.order}/payments/${p.id}/cashier-confirm/`))}>
                  Деньги в кассе
                </Button>
                <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`))}>
                  Отклонить
                </Button>
              </div>
            </div>
          ))}
          {list.length === 0 && (
            <p className="py-10 text-center text-sm text-[var(--muted-foreground)] lg:col-span-2">
              Оплат, ожидающих подтверждения кассой, нет.
            </p>
          )}
        </div>
      )}
    </AppShell>
  );
}

export default function CashierPage() {
  return <RequirePerm perm="payments.cashier" title="Касса"><CashierInner /></RequirePerm>;
}
