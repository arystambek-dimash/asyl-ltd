"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { SummaryCard } from "@/components/ui/summary-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { ErrorAlert } from "@/components/ui/data-state";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { useApi } from "@/lib/use-api";
import type { Department, ReportSummary } from "@/lib/types";
import { formatCurrency, monthStartLocalIsoDate, todayLocalIsoDate } from "@/lib/utils";
import { Scale } from "lucide-react";

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
  const incomeCurrency = data?.income.currency || "KZT";
  const shippedCurrency = data?.shipped.currency || "KZT";
  const debtCurrency = data?.debt_now.currency || "KZT";
  const incomeTotal = amountForCurrency(data?.income.by_currency ?? {}, data?.income.total ?? "0", incomeCurrency);
  const shippedTotal = amountForCurrency(
    data?.shipped.revenue_by_currency ?? {},
    data?.shipped.revenue ?? "0",
    shippedCurrency,
  );
  const debtTotal = amountForCurrency(data?.debt_now.by_currency ?? {}, data?.debt_now.total ?? "0", debtCurrency);
  const periodDebtByCurrency = data?.shipped.debt_amount_by_currency ?? {};
  const periodDebtCurrency = primaryMoneyCurrency(periodDebtByCurrency, shippedCurrency);
  const periodDebtTotal = amountForCurrency(periodDebtByCurrency, data?.shipped.debt_amount ?? "0", periodDebtCurrency);
  const incomeOthers = otherCurrencyAmounts(data?.income.by_currency ?? {}, incomeCurrency);
  const shippedOthers = otherCurrencyAmounts(data?.shipped.revenue_by_currency ?? {}, shippedCurrency);
  const debtOthers = otherCurrencyAmounts(data?.debt_now.by_currency ?? {}, debtCurrency);
  const periodDebtOthers = otherCurrencyAmounts(periodDebtByCurrency, periodDebtCurrency);

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
            value={formatCurrency(incomeTotal, incomeCurrency)}
            rows={[
              {
                label: "Наличные",
                value: formatCurrency(
                  amountForCurrency(data?.income.cash_by_currency ?? {}, data?.income.cash ?? "0", incomeCurrency),
                  incomeCurrency,
                ),
              },
              {
                label: "Безналичные",
                value: formatCurrency(
                  amountForCurrency(
                    data?.income.cashless_by_currency ?? {},
                    data?.income.cashless ?? "0",
                    incomeCurrency,
                  ),
                  incomeCurrency,
                ),
              },
              ...incomeOthers.map(([currency, value]) => ({
                label: "Также поступило",
                value: formatCurrency(value, currency),
              })),
            ]}
          />
          <SummaryCard
            title="Отгружено"
            tone="plain"
            value={formatCurrency(shippedTotal, shippedCurrency)}
            rows={[
              ...shippedOthers.map(([currency, value]) => ({
                label: "Также отгружено",
                value: formatCurrency(value, currency),
              })),
              { label: "Заказов", value: String(data?.shipped.orders ?? 0) },
              { label: "Мешков", value: String(data?.shipped.bags ?? 0) },
            ]}
          />
          <SummaryCard
            title="Долги"
            tone="destructive"
            value={formatCurrency(debtTotal, debtCurrency)}
            rows={[
              ...debtOthers.map(([currency, value]) => ({
                label: "Также в долге",
                value: formatCurrency(value, currency),
              })),
              {
                label: "Ушло в долг за период",
                value: formatCurrency(periodDebtTotal, periodDebtCurrency),
              },
              ...periodDebtOthers.map(([currency, value]) => ({
                label: "Также ушло в долг",
                value: formatCurrency(value, currency),
              })),
              { label: "Заказов в долге сейчас", value: String(data?.debt_now.orders ?? 0) },
            ]}
          />
          <SummaryCard
            title="Итого"
            tone="primary"
            value={formatCurrency(incomeTotal, incomeCurrency)}
            rows={[
              { label: "Отгружено", value: formatCurrency(shippedTotal, shippedCurrency) },
              { label: "Поступило", value: formatCurrency(incomeTotal, incomeCurrency), strong: true },
            ]}
          />
        </div>

        {/* Фильтры периода */}
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
