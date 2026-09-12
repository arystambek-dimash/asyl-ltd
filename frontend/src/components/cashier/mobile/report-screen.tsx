"use client";
import { useState } from "react";
import { CalendarDays } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { ErrorAlert } from "@/components/ui/data-state";
import { DonutChart } from "@/components/ui/donut-chart";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Tabs } from "@/components/ui/tabs";
import { formatCurrency, formatIsoDate, pluralRu } from "@/lib/utils";
import { filtersError, PERIOD_PRESETS, periodPresetOf, periodRange } from "../filters";
import type { CashierModel } from "../use-cashier";
import { reportSegments, type Breakdown } from "./report-segments";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[13px]">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}

/** Отчёт по поступлениям: период чипами, кольцо с итогом, разрез по отделам или способам, легенда строками. */
export function ReportScreen({ model }: { model: CashierModel }) {
  const { income, incomeReady, summary, filtersByScreen, patchFilters } = model;
  const [breakdown, setBreakdown] = useState<Breakdown>("departments");
  const [customOpen, setCustomOpen] = useState(false);
  const filters = filtersByScreen.report;
  const preset = periodPresetOf(filters);
  const rangeError = filtersError(filters);
  const { segments, counts } = reportSegments(summary.data, breakdown, income.currency);
  const positiveTotal = segments.reduce((sum, segment) => sum + Math.max(segment.value, 0), 0);
  const money = (value: number, currency = income.currency) => formatCurrency(value, currency);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex gap-2 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {PERIOD_PRESETS.map((item) => (
          <Chip key={item.key} active={preset === item.key} onClick={() => patchFilters(periodRange(item.key))}>
            {item.label}
          </Chip>
        ))}
        <Chip active={preset === "custom"} onClick={() => setCustomOpen(true)}>
          <CalendarDays className="size-3.5" />
          {preset === "custom"
            ? `${filters.dateFrom ? formatIsoDate(filters.dateFrom) : "…"} – ${filters.dateTo ? formatIsoDate(filters.dateTo) : "…"}`
            : "Период…"}
        </Chip>
      </div>

      {summary.error && <ErrorAlert message={summary.error} onRetry={summary.reload} />}

      <Card>
        <CardContent className="flex flex-col items-stretch gap-4 p-5">
          <DonutChart
            segments={segments}
            centerValue={incomeReady ? money(income.total) : "—"}
            centerLabel={
              summary.loading
                ? "Загрузка…"
                : incomeReady
                  ? `${income.payments} ${pluralRu(income.payments, ["оплата", "оплаты", "оплат"])}`
                  : ""
            }
            emptyLabel="За период поступлений нет"
          />
          <Tabs
            variant="segment"
            label="Разрез отчёта"
            className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
            tabs={[
              { key: "departments", label: "По отделам" },
              { key: "methods", label: "По способу" },
            ]}
            active={breakdown}
            onChange={(key) => setBreakdown(key as Breakdown)}
          />
          {incomeReady && positiveTotal === 0 ? (
            <p className="text-center text-sm text-[var(--muted-foreground)]">За период поступлений нет.</p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {segments.map((segment) => {
                const share =
                  positiveTotal > 0 && segment.value > 0 ? Math.round((segment.value / positiveTotal) * 100) : 0;
                const count = counts[segment.key];
                return (
                  <li key={segment.key} className="flex items-center gap-3 py-2.5">
                    <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: segment.color }} />
                    <span className="min-w-0 flex-1 truncate text-[15px]">{segment.label}</span>
                    <span className="text-right">
                      <span className="block text-[15px] font-semibold tabular-nums">{money(segment.value)}</span>
                      <span className="block text-xs text-[var(--muted-foreground)]">
                        {share}%
                        {count !== undefined ? ` · ${count} ${pluralRu(count, ["оплата", "оплаты", "оплат"])}` : ""}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {incomeReady && (income.refunded > 0 || income.otherCurrencies.length > 0 || income.otherRefunds.length > 0) && (
        <Card>
          <CardContent className="flex flex-col gap-2 p-4">
            {income.refunded > 0 && (
              <>
                <Row label="Поступило до возвратов" value={money(income.gross)} />
                <Row label="Возвращено" value={money(income.refunded)} />
              </>
            )}
            {income.otherCurrencies.map(([currency, value]) => (
              <Row key={currency} label="Также чистыми" value={money(value, currency)} />
            ))}
            {income.otherRefunds.flatMap(([currency, value]) => [
              <Row
                key={`${currency}-gross`}
                label={`Поступило до возвратов, ${currency}`}
                value={money(income.grossFor(currency), currency)}
              />,
              <Row key={`${currency}-refund`} label={`Возвращено, ${currency}`} value={money(value, currency)} />,
            ])}
          </CardContent>
        </Card>
      )}

      <Modal
        variant="sheet"
        open={customOpen}
        onClose={() => setCustomOpen(false)}
        eyebrow="Отчёт"
        title="Свой период"
        footer={<Button onClick={() => setCustomOpen(false)}>Готово</Button>}
      >
        <div className="grid grid-cols-2 gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">С даты</span>
            <Input type="date" value={filters.dateFrom} onChange={(e) => patchFilters({ dateFrom: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
            <Input type="date" value={filters.dateTo} onChange={(e) => patchFilters({ dateTo: e.target.value })} />
          </label>
        </div>
        {rangeError && <p className="mt-3 text-xs font-medium text-[var(--destructive)]">{rangeError}</p>}
      </Modal>
    </section>
  );
}
