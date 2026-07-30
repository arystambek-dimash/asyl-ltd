"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { SummaryCard } from "@/components/ui/summary-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import type { Department } from "@/lib/types";
import { formatCurrency, monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";
import { Scale } from "lucide-react";

/* Ответ GET /reports/summary/ — все деньги считаются на сервере. */
interface ReportDay {
  date: string;
  orders: number;
  bags: number;
  revenue: string;
  debt_amount: string;
  cash: string;
  cashless: string;
  received: string;
  payments: number;
}

interface ReportSummary {
  income: { total: string; cash: string; cashless: string; payments: number };
  shipped: { revenue: string; orders: number; bags: number; debt_amount: string };
  debt_now: { total: string; orders: number };
  days: ReportDay[];
}

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

const money = formatCurrency;

function EmptyRow({ colSpan }: { colSpan: number }) {
  return (
    <TR>
      <TD colSpan={colSpan} className="py-14 text-center text-sm text-[var(--muted-foreground)]">
        Здесь пусто
      </TD>
    </TR>
  );
}

function DaysTable({ data }: { data: ReportSummary }) {
  const cols = ["№", "Дата", "Заказов", "Мешков", "Отгружено", "Наличные", "Безналичные", "Поступило", "В долг"];
  return (
    <div>
      <div className="space-y-3 md:hidden">
        {data.days.length === 0 ? (
          <div className="rounded-2xl border border-[var(--border)] bg-[var(--card)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
            Здесь пусто
          </div>
        ) : (
          <>
            {data.days.map((day) => (
              <article
                key={day.date}
                className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">
                      Смена за день
                    </p>
                    <h3 className="mt-1 font-semibold tabular-nums">{dayLabel(day.date)}</h3>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-[var(--muted-foreground)]">Отгружено</p>
                    <p className="mt-1 font-semibold tabular-nums">{money(day.revenue)}</p>
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-[var(--border)] pt-4 text-sm">
                  <div>
                    <dt className="text-xs text-[var(--muted-foreground)]">Заказы / мешки</dt>
                    <dd className="mt-1 font-medium tabular-nums">
                      {day.orders} / {day.bags}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-[var(--muted-foreground)]">Поступило</dt>
                    <dd className="mt-1 font-semibold tabular-nums text-[var(--success)]">{money(day.received)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-[var(--muted-foreground)]">Наличные</dt>
                    <dd className="mt-1 tabular-nums">{money(day.cash)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-[var(--muted-foreground)]">Безналичные</dt>
                    <dd className="mt-1 tabular-nums">{money(day.cashless)}</dd>
                  </div>
                </dl>
                {Number(day.debt_amount) > 0 && (
                  <div className="mt-3 flex items-center justify-between rounded-xl bg-[var(--soft-red)] px-3 py-2 text-sm">
                    <span className="text-[var(--soft-red-foreground)]">Отгружено в долг</span>
                    <strong className="tabular-nums text-[var(--destructive)]">{money(day.debt_amount)}</strong>
                  </div>
                )}
              </article>
            ))}
            <article className="rounded-2xl border border-[var(--primary)]/20 bg-[var(--primary)] p-4 text-[var(--primary-foreground)] shadow-card">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] opacity-70">Итого за период</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">{money(data.income.total)}</p>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="opacity-70">Отгружено</dt>
                  <dd className="mt-1 font-medium tabular-nums">{money(data.shipped.revenue)}</dd>
                </div>
                <div>
                  <dt className="opacity-70">Заказы / мешки</dt>
                  <dd className="mt-1 font-medium tabular-nums">
                    {data.shipped.orders} / {data.shipped.bags}
                  </dd>
                </div>
              </dl>
            </article>
          </>
        )}
      </div>

      <div className="hidden overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card md:block">
        <Table>
          <THead>
            <TR>
              {cols.map((c, i) => (
                <TH key={c} className={i >= 2 ? "text-right" : ""}>
                  {c}
                </TH>
              ))}
            </TR>
          </THead>
          <TBody>
            {data.days.length === 0 ? (
              <EmptyRow colSpan={cols.length} />
            ) : (
              <>
                {data.days.map((d, i) => (
                  <TR key={d.date}>
                    <TD className="text-[var(--muted-foreground)]">{i + 1}</TD>
                    <TD className="font-medium tabular-nums">{dayLabel(d.date)}</TD>
                    <TD className="text-right tabular-nums">{d.orders}</TD>
                    <TD className="text-right tabular-nums">{d.bags}</TD>
                    <TD className="text-right tabular-nums">{money(d.revenue)}</TD>
                    <TD className="text-right tabular-nums">{money(d.cash)}</TD>
                    <TD className="text-right tabular-nums">{money(d.cashless)}</TD>
                    <TD className="text-right font-semibold tabular-nums text-[var(--success)]">{money(d.received)}</TD>
                    <TD className="text-right tabular-nums text-[var(--destructive)]">{money(d.debt_amount)}</TD>
                  </TR>
                ))}
                <TR className="bg-[var(--muted)]/50">
                  <TD colSpan={2} className="font-semibold">
                    Итого
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.orders}</TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.bags}</TD>
                  <TD className="text-right font-semibold tabular-nums">{money(data.shipped.revenue)}</TD>
                  <TD className="text-right font-semibold tabular-nums">{money(data.income.cash)}</TD>
                  <TD className="text-right font-semibold tabular-nums">{money(data.income.cashless)}</TD>
                  <TD className="text-right font-semibold tabular-nums text-[var(--success)]">
                    {money(data.income.total)}
                  </TD>
                  <TD className="text-right font-semibold tabular-nums text-[var(--destructive)]">
                    {money(data.shipped.debt_amount)}
                  </TD>
                </TR>
              </>
            )}
          </TBody>
        </Table>
      </div>
    </div>
  );
}

function ReportsPageInner() {
  const [from, setFrom] = useState(monthStartLocalIsoDate());
  const [to, setTo] = useState(todayLocalIsoDate());
  const [department, setDepartment] = useState("all");

  const { data: departments } = useApi<Department[]>("/departments/");

  const url = useMemo(() => {
    const q = new URLSearchParams();
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    if (department !== "all") q.set("department", department);
    const qs = q.toString();
    return `/reports/summary/${qs ? `?${qs}` : ""}`;
  }, [from, to, department]);

  const { data, error, reload } = useApi<ReportSummary>(url);

  return (
    <AppShell
      title="Отчёты"
      section="Обзор"
      description="Касса и отгрузки за период: поступления, долги и движение денег."
    >
      <div className="flex flex-col gap-5">
        {/* Сводка периода — как в кассовых отчётах: значение + расшифровка. */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            title="Поступления"
            tone="success"
            value={money(data?.income.total ?? 0)}
            rows={[
              { label: "Наличные", value: money(data?.income.cash ?? 0) },
              { label: "Безналичные", value: money(data?.income.cashless ?? 0) },
            ]}
          />
          <SummaryCard
            title="Отгружено"
            tone="plain"
            value={money(data?.shipped.revenue ?? 0)}
            rows={[
              { label: "Заказов", value: String(data?.shipped.orders ?? 0) },
              { label: "Мешков", value: String(data?.shipped.bags ?? 0) },
            ]}
          />
          <SummaryCard
            title="Долги"
            tone="destructive"
            value={money(data?.debt_now.total ?? 0)}
            rows={[
              { label: "Ушло в долг за период", value: money(data?.shipped.debt_amount ?? 0) },
              { label: "Заказов в долге сейчас", value: String(data?.debt_now.orders ?? 0) },
            ]}
          />
          <SummaryCard
            title="Итого"
            tone="primary"
            value={money(data?.income.total ?? 0)}
            rows={[
              { label: "Отгружено", value: money(data?.shipped.revenue ?? 0) },
              { label: "Поступило", value: money(data?.income.total ?? 0), strong: true },
            ]}
          />
        </div>

        {/* Фильтры периода */}
        <div className="grid grid-cols-1 items-end gap-3 sm:flex sm:flex-wrap sm:gap-x-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="reports-date-from" className="text-xs font-medium text-[var(--muted-foreground)]">
              С даты
            </label>
            <Input
              id="reports-date-from"
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="w-full sm:w-[170px]"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="reports-date-to" className="text-xs font-medium text-[var(--muted-foreground)]">
              По дату
            </label>
            <Input
              id="reports-date-to"
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="w-full sm:w-[170px]"
            />
          </div>
          {(departments?.length ?? 0) > 0 && (
            <FilterDropdown
              label="Отдел"
              active={department}
              onChange={setDepartment}
              options={[
                { key: "all", label: "Все" },
                ...(departments ?? []).map((row) => ({ key: row.code, label: row.name })),
              ]}
            />
          )}
        </div>

        {error && !data && <ErrorAlert message={error} onRetry={reload} />}

        {data && <DaysTable data={data} />}

        <p className="flex items-start gap-1.5 text-xs text-[var(--muted-foreground)]">
          <Scale className="mt-0.5 size-3.5 shrink-0" />
          Поступление — оплата, подтверждённая кассой, на дату подтверждения. Отгрузка — по дате выезда машины.
          Удалённые заказы не учитываются.
        </p>
      </div>
    </AppShell>
  );
}

export default function ReportsPage() {
  return (
    <RequirePerm perm="reports.view" title="Отчёты">
      <ReportsPageInner />
    </RequirePerm>
  );
}
