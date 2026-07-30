"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Scale } from "lucide-react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { ClientsTable } from "@/components/reports/clients-table";
import { CHART_TOOLTIP_STYLE } from "@/components/ui/chart-tooltip";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { SummaryCard } from "@/components/ui/summary-card";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { paidSplit, reportChartSeries } from "@/lib/report-analytics";
import { useApi } from "@/lib/use-api";
import type { Department, ReportSummary } from "@/lib/types";
import {
  currencySymbol,
  formatCompactCurrency,
  formatCurrency,
  formatMoney,
  monthStartLocalIsoDate,
  todayLocalIsoDate,
} from "@/lib/utils";

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function EmptyRow({ colSpan }: { colSpan: number }) {
  return (
    <TR>
      <TD colSpan={colSpan} className="py-14 text-center text-sm text-[var(--muted-foreground)]">
        Здесь пусто
      </TD>
    </TR>
  );
}

/* ── История периода: три смысловые карточки ────────────────────────────── */

function PeriodStory({ data }: { data: ReportSummary }) {
  const split = paidSplit(data.shipped);
  const incomeCurrency = data.income.currency || "KZT";
  const incomeTotal = amountForCurrency(data.income.by_currency, data.income.total, incomeCurrency);
  const incomeOthers = otherCurrencyAmounts(data.income.by_currency, incomeCurrency);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <SummaryCard
        title="Отгружено за период"
        tone="plain"
        value={formatCompactCurrency(split.revenue, split.currency)}
        valueTitle={formatCurrency(split.revenue, split.currency)}
        rows={[
          ...split.others.map((other) => ({
            label: "Также отгружено",
            value: formatCurrency(other.revenue, other.currency),
          })),
          { label: "Заказов", value: formatMoney(data.shipped.orders) },
          { label: "Мешков", value: formatMoney(data.shipped.bags) },
        ]}
      />
      <SummaryCard
        title="Из отгруженного — в долг"
        tone="destructive"
        value={formatCompactCurrency(split.debt, split.currency)}
        valueTitle={formatCurrency(split.debt, split.currency)}
        extra={
          split.debtSharePct != null ? (
            <div className="mt-3">
              <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--muted)]">
                <div className="h-full bg-[var(--destructive)]/80" style={{ width: `${split.debtSharePct}%` }} />
              </div>
              <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">
                {split.debtSharePct}% отгрузок ушло в долг
              </p>
            </div>
          ) : undefined
        }
        rows={[
          {
            label: "Оплачено сразу",
            value: formatCurrency(split.paidNow, split.currency),
            strong: true,
          },
          ...split.others.map((other) => ({
            label: "Также в долг",
            value: formatCurrency(other.debt, other.currency),
          })),
        ]}
      />
      <SummaryCard
        title="Касса получила за период"
        tone="success"
        value={formatCompactCurrency(incomeTotal, incomeCurrency)}
        valueTitle={formatCurrency(incomeTotal, incomeCurrency)}
        rows={[
          {
            label: "Наличные",
            value: formatCurrency(
              amountForCurrency(data.income.cash_by_currency, data.income.cash, incomeCurrency),
              incomeCurrency,
            ),
          },
          {
            label: "Безналичные",
            value: formatCurrency(
              amountForCurrency(data.income.cashless_by_currency, data.income.cashless, incomeCurrency),
              incomeCurrency,
            ),
          },
          ...incomeOthers.map(([currency, value]) => ({
            label: "Также поступило",
            value: formatCurrency(value, currency),
          })),
          { label: "Платежей", value: formatMoney(data.income.payments) },
        ]}
      />
    </div>
  );
}

/* ── Долг сейчас: снимок на сегодня, от периода не зависит ──────────────── */

function DebtNowBand({ debt }: { debt: ReportSummary["debt_now"] }) {
  const currency = debt.currency || "KZT";
  const total = amountForCurrency(debt.by_currency, debt.total, currency);
  const others = otherCurrencyAmounts(debt.by_currency, currency);
  const overdueCurrency = primaryMoneyCurrency(debt.overdue_by_currency, debt.overdue_currency || currency);
  const overdueTotal = amountForCurrency(debt.overdue_by_currency, "0", overdueCurrency);
  const hasOverdue = overdueTotal > 0 || debt.overdue_clients > 0;

  return (
    <section className="flex flex-wrap items-center gap-x-10 gap-y-4 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-card">
      <div className="min-w-[180px]">
        <div className="text-[13px] font-medium text-[var(--muted-foreground)]">Долг клиентов сейчас</div>
        <div
          title={formatCurrency(total, currency)}
          className="mt-1 text-[26px] font-bold leading-none tracking-tight tabular-nums text-[var(--destructive)]"
        >
          {formatCompactCurrency(total, currency)}
        </div>
        <div className="mt-1.5 text-xs text-[var(--muted-foreground)]">
          {others.map(([other, value]) => `+ ${formatCurrency(value, other)} · `)}
          {formatMoney(debt.orders)} заказов · снимок на сегодня, от периода не зависит
        </div>
      </div>
      <div>
        <div className="text-[13px] font-medium text-[var(--muted-foreground)]">Просрочено</div>
        {hasOverdue ? (
          <>
            <div
              title={formatCurrency(overdueTotal, overdueCurrency)}
              className="mt-1 text-lg font-bold leading-none tabular-nums text-[var(--destructive)]"
            >
              {formatCompactCurrency(overdueTotal, overdueCurrency)}
            </div>
            <div className="mt-1.5 text-xs text-[var(--muted-foreground)]">
              у {formatMoney(debt.overdue_clients)} клиентов — окно оплаты уже открыто
            </div>
          </>
        ) : (
          <div className="mt-1 text-lg font-semibold leading-none text-[var(--success)]">Просрочки нет</div>
        )}
      </div>
      <Link
        href="/accounting"
        className="ml-auto inline-flex items-center gap-1.5 text-sm font-medium text-[var(--ring)] hover:underline"
      >
        Открыть долги
        <ArrowRight className="size-4" />
      </Link>
    </section>
  );
}

/* ── График по дням ─────────────────────────────────────────────────────── */

function DaysChart({ data }: { data: ReportSummary }) {
  const currency = data.shipped.currency || "KZT";
  const series = reportChartSeries(data.days, currency);
  if (series.length < 2) return null;

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] pb-2 shadow-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-5 pt-5">
        <h3 className="text-sm font-semibold tracking-tight">По дням, {currencySymbol(currency)}</h3>
        <div className="flex items-center gap-4 text-xs text-[var(--muted-foreground)]">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-[var(--ring)]" /> Отгружено
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-[var(--success)]" /> Поступило в кассу
          </span>
        </div>
      </div>
      <div
        className="h-[210px] w-full px-2 pt-2 sm:px-4"
        role="img"
        aria-label={`График отгрузок и поступлений по дням за период, ${series.length} дней.`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 16, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="report-revenue-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.22} />
                <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 7" vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              interval={series.length > 14 ? 3 : 1}
              dy={8}
            />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              formatter={(value: number, name: string) => [
                formatCurrency(value, currency),
                name === "revenue" ? "Отгружено" : "Поступило",
              ]}
              labelFormatter={(label) => String(label)}
            />
            <Area
              type="monotone"
              dataKey="revenue"
              stroke="var(--ring)"
              strokeWidth={2.25}
              fill="url(#report-revenue-fill)"
            />
            <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={2} fillOpacity={0} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <ul className="sr-only">
        {series.map((point) => (
          <li key={point.date}>
            {point.label}: отгружено {formatCurrency(point.revenue, currency)}, поступило{" "}
            {formatCurrency(point.received, currency)}
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── Таблица по дням: точные числа для сверки ───────────────────────────── */

function DaysTable({ data }: { data: ReportSummary }) {
  const cols = ["№", "Дата", "Заказов", "Мешков", "Отгружено", "Наличные", "Безналичные", "Поступило", "В долг"];
  const shippedCurrency = data.shipped.currency || "KZT";
  const incomeCurrency = data.income.currency || "KZT";
  return (
    <div>
      <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
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
                    <TD className="text-right tabular-nums">
                      <CurrencyAmounts
                        byCurrency={d.revenue_by_currency}
                        fallbackAmount={d.revenue}
                        fallbackCurrency={shippedCurrency}
                      />
                    </TD>
                    <TD className="text-right tabular-nums">
                      <CurrencyAmounts
                        byCurrency={d.cash_by_currency}
                        fallbackAmount={d.cash}
                        fallbackCurrency={incomeCurrency}
                      />
                    </TD>
                    <TD className="text-right tabular-nums">
                      <CurrencyAmounts
                        byCurrency={d.cashless_by_currency}
                        fallbackAmount={d.cashless}
                        fallbackCurrency={incomeCurrency}
                      />
                    </TD>
                    <TD className="text-right font-semibold tabular-nums text-[var(--success)]">
                      <CurrencyAmounts
                        byCurrency={d.received_by_currency}
                        fallbackAmount={d.received}
                        fallbackCurrency={incomeCurrency}
                      />
                    </TD>
                    <TD className="text-right tabular-nums text-[var(--destructive)]">
                      <CurrencyAmounts
                        byCurrency={d.debt_amount_by_currency}
                        fallbackAmount={d.debt_amount}
                        fallbackCurrency={shippedCurrency}
                      />
                    </TD>
                  </TR>
                ))}
                <TR className="bg-[var(--muted)]/50">
                  <TD colSpan={2} className="font-semibold">
                    Итого
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.orders}</TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.bags}</TD>
                  <TD className="text-right font-semibold tabular-nums">
                    <CurrencyAmounts
                      byCurrency={data.shipped.revenue_by_currency}
                      fallbackAmount={data.shipped.revenue}
                      fallbackCurrency={shippedCurrency}
                    />
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">
                    <CurrencyAmounts
                      byCurrency={data.income.cash_by_currency}
                      fallbackAmount={data.income.cash}
                      fallbackCurrency={incomeCurrency}
                    />
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">
                    <CurrencyAmounts
                      byCurrency={data.income.cashless_by_currency}
                      fallbackAmount={data.income.cashless}
                      fallbackCurrency={incomeCurrency}
                    />
                  </TD>
                  <TD className="text-right font-semibold tabular-nums text-[var(--success)]">
                    <CurrencyAmounts
                      byCurrency={data.income.by_currency}
                      fallbackAmount={data.income.total}
                      fallbackCurrency={incomeCurrency}
                    />
                  </TD>
                  <TD className="text-right font-semibold tabular-nums text-[var(--destructive)]">
                    <CurrencyAmounts
                      byCurrency={data.shipped.debt_amount_by_currency}
                      fallbackAmount={data.shipped.debt_amount}
                      fallbackCurrency={shippedCurrency}
                    />
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
  // По клиентам — основной разрез: должников ищут по имени, а не по дате.
  const [view, setView] = useState<"clients" | "days">("clients");

  const { data: departments } = useApi<Department[]>("/departments/");
  const validRange = !from || !to || from <= to;

  const url = useMemo(() => {
    if (!validRange) return null;
    const q = new URLSearchParams();
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    if (department !== "all") q.set("department", department);
    const qs = q.toString();
    return `/reports/summary/${qs ? `?${qs}` : ""}`;
  }, [from, to, department, validRange]);

  const { data, error, reload } = useApi<ReportSummary>(url);

  return (
    <AppShell
      title="Отчёты"
      section="Обзор"
      description="История периода: сколько отгрузили, сколько ушло в долг и сколько денег получила касса."
    >
      <div className="flex flex-col gap-5">
        {/* Период задаёт всё, что ниже, поэтому фильтры стоят первыми. */}
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">С даты</span>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="h-9 w-[160px]" />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="h-9 w-[160px]" />
          </label>
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

        {!validRange && (
          <p role="alert" className="text-sm font-medium text-[var(--destructive)]">
            Дата начала не может быть позже даты окончания.
          </p>
        )}
        {error && <ErrorAlert message={error} onRetry={reload} />}

        {data && (
          <>
            <PeriodStory data={data} />
            <DebtNowBand debt={data.debt_now} />
            <DaysChart data={data} />
            <div className="flex flex-col gap-3">
              <Tabs
                tabs={[
                  { key: "clients", label: "По клиентам" },
                  { key: "days", label: "По дням" },
                ]}
                active={view}
                onChange={(key) => setView(key as "clients" | "days")}
              />
              {view === "clients" ? <ClientsTable clients={data.clients ?? []} /> : <DaysTable data={data} />}
            </div>
          </>
        )}

        <p className="flex items-start gap-1.5 text-xs text-[var(--muted-foreground)]">
          <Scale className="mt-0.5 size-3.5 shrink-0" />
          Поступление — оплата, подтверждённая кассой, на дату подтверждения. Отгрузка — по дате выезда машины. «Долг
          сейчас» — снимок на сегодня по всем заказам. Удалённые заказы не учитываются. Наведите на крупное число, чтобы
          увидеть его точное значение.
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
