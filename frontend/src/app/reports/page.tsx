"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Scale } from "lucide-react";
import { MoneyTrendChart } from "@/components/charts/money-trend-chart";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { ClientsTable } from "@/components/reports/clients-table";
import { DepartmentComparison } from "@/components/reports/department-comparison";
import { LoadMore } from "@/components/ui/load-more";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { SummaryCard } from "@/components/ui/summary-card";
import { EmptyRow, Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { adaptDashboardDebt } from "@/lib/dashboard-analytics";
import {
  incomeDetailRows,
  incomeTotals,
  reportChartCurrencies,
  reportChartSeries,
  shipmentSettlement,
} from "@/lib/report-analytics";
import { useApi } from "@/lib/use-api";
import type { Department, ReportDay, ReportSummary } from "@/lib/types";
import {
  apiUrl,
  cn,
  currencySymbol,
  dateRangeError,
  formatCompactCurrency,
  formatCurrency,
  formatIsoDate,
  formatMoney,
  monthStartLocalIsoDate,
  todayLocalIsoDate,
} from "@/lib/utils";

/* ── История периода: три смысловые карточки ────────────────────────────── */

function PeriodStory({ data }: { data: ReportSummary }) {
  const split = shipmentSettlement(data.shipped);
  const income = incomeTotals(data);

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
        title="Остаток долга по отгрузкам периода"
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
                {split.debtSharePct}% стоимости этих отгрузок остаётся в долгу
              </p>
            </div>
          ) : undefined
        }
        rows={[
          {
            label: "Погашено к текущему моменту",
            value: formatCurrency(split.paidToDate, split.currency),
            strong: true,
          },
          ...split.others.flatMap((other) => [
            {
              label: `Погашено, ${other.currency}`,
              value: formatCurrency(other.paidToDate, other.currency),
            },
            {
              label: `Остаток долга, ${other.currency}`,
              value: formatCurrency(other.debt, other.currency),
            },
          ]),
        ]}
      />
      <SummaryCard
        title="Чистое поступление в кассу за период"
        tone={income.total < 0 ? "destructive" : "success"}
        value={formatCompactCurrency(income.total, income.currency)}
        valueTitle={formatCurrency(income.total, income.currency)}
        rows={[
          { label: "Наличными, с учётом возвратов", value: formatCurrency(income.cash, income.currency) },
          { label: "Безналично, с учётом возвратов", value: formatCurrency(income.cashless, income.currency) },
          ...incomeDetailRows(income),
          { label: "Платежей", value: formatMoney(income.payments) },
          ...(data.income.refunds > 0 ? [{ label: "Возвратов", value: formatMoney(data.income.refunds) }] : []),
        ]}
      />
    </div>
  );
}

/* ── Долг сейчас: снимок на сегодня, от периода не зависит ──────────────── */

function DebtNowBand({ debt }: { debt: ReportSummary["debt_now"] }) {
  const {
    debtTotal: total,
    debtCurrency: currency,
    debtOthers: others,
    overdueTotal,
    overdueCurrency,
    overdueOthers,
  } = adaptDashboardDebt(debt);
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
              {overdueOthers.map(([other, value]) => `+ ${formatCurrency(value, other)} · `)}у{" "}
              {formatMoney(debt.overdue_clients)} клиентов — окно оплаты уже открыто
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
  const currencies = useMemo(() => reportChartCurrencies(data), [data]);
  const [requestedCurrency, setRequestedCurrency] = useState<string | null>(null);
  // Если после смены периода выбранной валюты больше нет, берём доступную
  // прямо при рендере — отдельный effect и лишний цикл рендера не нужны.
  const currency = requestedCurrency && currencies.includes(requestedCurrency) ? requestedCurrency : currencies[0];
  const series = reportChartSeries(data.days, currency);
  if (series.length < 2) return null;

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] pb-2 shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5">
        <h3 className="text-sm font-semibold tracking-tight">По дням, {currencySymbol(currency)}</h3>
        <div className="flex flex-wrap items-center gap-4 text-xs text-[var(--muted-foreground)]">
          {currencies.length > 1 && (
            <FilterDropdown
              label="Валюта"
              active={currency}
              onChange={setRequestedCurrency}
              options={currencies.map((code) => ({ key: code, label: code }))}
            />
          )}
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-[var(--ring)]" /> Выручка
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full bg-[var(--success)]" /> Поступило
          </span>
        </div>
      </div>
      <MoneyTrendChart data={series} currency={currency} className="h-[210px] w-full px-2 pt-2 sm:px-4" />
    </section>
  );
}

/* ── Таблица по дням: точные числа для сверки ───────────────────────────── */

interface MoneyCell {
  byCurrency: Record<string, string>;
  amount: string;
}

/**
 * Денежные колонки таблицы по дням: значение дня и строки «Итого» рядом,
 * чтобы новая колонка добавлялась в одном месте. Отгрузочные суммы без
 * разбивки по валютам показываются в валюте отгрузок, кассовые — в валюте поступлений.
 */
const DAY_MONEY_COLUMNS: {
  title: string;
  source: "shipped" | "income";
  className?: string;
  day: (day: ReportDay) => MoneyCell;
  total: (data: ReportSummary) => MoneyCell;
}[] = [
  {
    title: "Отгружено",
    source: "shipped",
    day: (d) => ({ byCurrency: d.revenue_by_currency, amount: d.revenue }),
    total: ({ shipped }) => ({ byCurrency: shipped.revenue_by_currency, amount: shipped.revenue }),
  },
  {
    title: "Погашено",
    source: "shipped",
    day: (d) => ({ byCurrency: d.paid_amount_by_currency, amount: d.paid_amount }),
    total: ({ shipped }) => ({ byCurrency: shipped.paid_amount_by_currency, amount: shipped.paid_amount }),
  },
  {
    title: "Остаток долга сейчас",
    source: "shipped",
    className: "text-[var(--destructive)]",
    day: (d) => ({ byCurrency: d.debt_amount_by_currency, amount: d.debt_amount }),
    total: ({ shipped }) => ({ byCurrency: shipped.debt_amount_by_currency, amount: shipped.debt_amount }),
  },
  {
    title: "Наличные, нетто",
    source: "income",
    day: (d) => ({ byCurrency: d.cash_by_currency, amount: d.cash }),
    total: ({ income }) => ({ byCurrency: income.cash_by_currency, amount: income.cash }),
  },
  {
    title: "Безналичные, нетто",
    source: "income",
    day: (d) => ({ byCurrency: d.cashless_by_currency, amount: d.cashless }),
    total: ({ income }) => ({ byCurrency: income.cashless_by_currency, amount: income.cashless }),
  },
  {
    title: "Возвраты",
    source: "income",
    className: "text-[var(--destructive)]",
    day: (d) => ({ byCurrency: d.refunded_by_currency, amount: d.refunded }),
    total: ({ income }) => ({ byCurrency: income.refunded_by_currency, amount: income.refunded }),
  },
  {
    title: "Чистое поступление",
    source: "income",
    className: "font-semibold",
    day: (d) => ({ byCurrency: d.received_by_currency, amount: d.received }),
    total: ({ income }) => ({ byCurrency: income.by_currency, amount: income.total }),
  },
];

function DaysTable({ data }: { data: ReportSummary }) {
  const cols = ["№", "Дата", "Заказов", "Мешков", ...DAY_MONEY_COLUMNS.map((column) => column.title)];
  const fallbackCurrency = {
    shipped: data.shipped.currency || "KZT",
    income: data.income.currency || "KZT",
  };
  const moneyCells = (cell: (column: (typeof DAY_MONEY_COLUMNS)[number]) => MoneyCell, strong: boolean) =>
    DAY_MONEY_COLUMNS.map((column) => {
      const { byCurrency, amount } = cell(column);
      return (
        <TD key={column.title} className={cn("text-right tabular-nums", column.className, strong && "font-semibold")}>
          <CurrencyAmounts
            byCurrency={byCurrency}
            fallbackAmount={amount}
            fallbackCurrency={fallbackCurrency[column.source]}
          />
        </TD>
      );
    });
  // Длинный период рендерим лениво; строка «Итого» видна всегда.
  const [limit, setLimit] = useState(31);
  const visibleDays = data.days.slice(0, limit);
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
                {visibleDays.map((d, i) => (
                  <TR key={d.date}>
                    <TD className="text-[var(--muted-foreground)]">{i + 1}</TD>
                    <TD className="font-medium tabular-nums">{formatIsoDate(d.date)}</TD>
                    <TD className="text-right tabular-nums">{d.orders}</TD>
                    <TD className="text-right tabular-nums">{d.bags}</TD>
                    {moneyCells((column) => column.day(d), false)}
                  </TR>
                ))}
                <TR className="bg-[var(--muted)]/50">
                  <TD colSpan={2} className="font-semibold">
                    Итого
                  </TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.orders}</TD>
                  <TD className="text-right font-semibold tabular-nums">{data.shipped.bags}</TD>
                  {moneyCells((column) => column.total(data), true)}
                </TR>
              </>
            )}
          </TBody>
        </Table>
        <LoadMore
          shown={visibleDays.length}
          total={data.days.length}
          hasMore={data.days.length > visibleDays.length}
          onClick={() => setLimit((current) => current + 31)}
        />
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
  const rangeError = dateRangeError(from, to);
  const url = rangeError ? null : apiUrl("/reports/summary/", { date_from: from, date_to: to, department });

  const { data, error, reload } = useApi<ReportSummary>(url);

  return (
    <AppShell
      title="Отчёты"
      section="Обзор"
      description="Отгрузки периода, их текущее погашение и чистое движение денег в кассе."
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

        {rangeError && (
          <p role="alert" className="text-sm font-medium text-[var(--destructive)]">
            {rangeError}
          </p>
        )}
        {error && <ErrorAlert message={error} onRetry={reload} />}

        {data && (
          <>
            <PeriodStory data={data} />
            <DepartmentComparison rows={data.departments} from={data.from} to={data.to} />
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
              {view === "clients" ? <ClientsTable clients={data.clients} /> : <DaysTable data={data} />}
            </div>
          </>
        )}

        <p className="flex items-start gap-1.5 text-xs text-[var(--muted-foreground)]">
          <Scale className="mt-0.5 size-3.5 shrink-0" />
          Поступление учитывается на дату подтверждения, возврат — на дату завершения; поэтому касса показана чистыми.
          Отгрузка относится к дате выезда. Остатки в карточке и таблицах — снимок на сейчас по заказам, отгруженным в
          выбранном периоде. «Долг клиентов сейчас» охватывает весь выбранный отдел и не ограничивается датами.
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
