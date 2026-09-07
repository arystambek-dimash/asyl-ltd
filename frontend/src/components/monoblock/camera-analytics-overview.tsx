"use client";

import { useId } from "react";
import { ArrowDown, CalendarDays, ChevronRight, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ColorDot, Panel } from "@/components/monoblock/ui";
import {
  AlwaysOnReceiptDestinationLabel,
  resolveAlwaysOnReceiptDestination,
  type AlwaysOnReceiptMappingContext,
} from "@/components/monoblock/always-on-production-panel";
import { fullDay, shortDay } from "@/lib/day-analytics";
import { colorMeta } from "@/lib/monoblock-colors";
import type { AlwaysOnDailyCameraAnalytics } from "@/lib/types";
import { cn, pluralRu } from "@/lib/utils";

export type AnalyticsDateRange = { from: string; to: string };

function previousDay(day: string, offset: number) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() - offset);
  return date.toISOString().slice(0, 10);
}

const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

export function CameraAnalyticsOverview({
  today,
  dateFrom,
  dateTo,
  onRangeChange,
  daily,
  loading,
  available,
  error,
  onRetry,
  isShipping,
  receiptMapping,
  selectedDay,
  onSelectDay,
}: {
  today: string;
  dateFrom: string;
  dateTo: string;
  onRangeChange: (range: AnalyticsDateRange | null) => void;
  daily?: AlwaysOnDailyCameraAnalytics;
  loading: boolean;
  available: boolean;
  error?: string;
  onRetry: () => void;
  isShipping: boolean;
  receiptMapping: AlwaysOnReceiptMappingContext;
  selectedDay: string | null;
  onSelectDay: (day: string | null) => void;
}) {
  const rangeErrorId = useId();
  const days = (Date.parse(dateTo) - Date.parse(dateFrom)) / 86_400_000 + 1;
  const valid = Number.isFinite(days) && days >= 1 && days <= 366;
  const isToday = dateFrom === today && dateTo === today;
  const singleDay = days === 1;
  const history = daily?.history ?? [];
  const colors = daily?.colors ?? [];
  const total = daily?.period_total;
  const adjustment = history.reduce((sum, point) => sum + point.adjustment, 0);
  const hasActivity = colors.length > 0 || history.some((point) => point.model_total > 0 || point.adjustment !== 0);
  const ready = valid && available && typeof total === "number";
  const chartMax = Math.max(1, ...history.map((point) => point.total));
  const daysWithBags = history.filter((point) => point.total > 0).length;
  const presets = [
    { label: "Сегодня", from: today, to: today },
    { label: "Вчера", from: previousDay(today, 1), to: previousDay(today, 1) },
    { label: "7 дней", from: previousDay(today, 6), to: today },
    { label: "30 дней", from: previousDay(today, 29), to: today },
  ];
  const dayPoint = singleDay ? history.find((point) => point.day === dateFrom) : undefined;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4" aria-label="Период аналитики">
        <div>
          <div className="mb-2 text-xs font-medium text-[var(--muted-foreground)]">Период</div>
          <div className="inline-flex flex-wrap gap-1 rounded-lg bg-[var(--muted)]/70 p-1">
            {presets.map((preset) => {
              const active = dateFrom === preset.from && dateTo === preset.to;
              return (
                <button
                  key={preset.label}
                  type="button"
                  aria-pressed={active}
                  onClick={() =>
                    onRangeChange(preset.label === "Сегодня" ? null : { from: preset.from, to: preset.to })
                  }
                  className={cn(
                    "min-h-9 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
                    active
                      ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                      : "text-[var(--muted-foreground)] hover:bg-[var(--card)]/60 hover:text-[var(--foreground)]",
                  )}
                >
                  {preset.label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="grid w-full grid-cols-2 gap-3 sm:w-auto">
          <label className="min-w-0 text-xs text-[var(--muted-foreground)]">
            С даты
            <Input
              type="date"
              aria-label="Аналитика с даты"
              aria-invalid={!valid}
              aria-describedby={!valid ? rangeErrorId : undefined}
              value={dateFrom}
              onChange={(event) => onRangeChange({ from: event.target.value, to: dateTo })}
              className="mt-2 h-11 w-full min-w-0 [color-scheme:light] sm:w-40 [.dark_&]:[color-scheme:dark]"
            />
          </label>
          <label className="min-w-0 text-xs text-[var(--muted-foreground)]">
            По дату
            <Input
              type="date"
              aria-label="Аналитика по дату"
              aria-invalid={!valid}
              aria-describedby={!valid ? rangeErrorId : undefined}
              value={dateTo}
              onChange={(event) => onRangeChange({ from: dateFrom, to: event.target.value })}
              className="mt-2 h-11 w-full min-w-0 [color-scheme:light] sm:w-40 [.dark_&]:[color-scheme:dark]"
            />
          </label>
        </div>
      </div>

      {!valid && (
        <p id={rangeErrorId} role="alert" className="text-sm text-[var(--destructive)]">
          Выберите период от 1 до 366 дней. Начало не должно быть позже окончания.
        </p>
      )}
      {valid && !loading && !available && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/5 px-4 py-3"
        >
          <div className="min-w-0 text-sm">
            <p className="font-medium">Не удалось получить итог за период</p>
            <p className="mt-1 text-[var(--muted-foreground)]">{error || "Подождите немного и обновите данные."}</p>
          </div>
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="size-3.5" /> Обновить
          </Button>
        </div>
      )}

      <Panel className="flex flex-wrap items-center justify-between gap-5 bg-[var(--muted)]/30 p-5 sm:p-6">
        <div aria-busy={loading}>
          <p className="text-sm text-[var(--muted-foreground)]">{isToday ? "Учтено сегодня" : "За выбранный период"}</p>
          <div className="mt-2 flex items-baseline gap-2.5">
            <span className="text-4xl font-semibold tracking-tight tabular-nums sm:text-5xl">
              {ready ? number.format(total) : "—"}
            </span>
            {ready && (
              <span className="text-sm text-[var(--muted-foreground)]">
                {pluralRu(total, ["мешок", "мешка", "мешков"])}
              </span>
            )}
          </div>
          <p className="mt-3 flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <CalendarDays className="size-3.5" />
            {valid ? (singleDay ? fullDay(dateFrom) : `${fullDay(dateFrom)} — ${fullDay(dateTo)}`) : "Выберите даты"}
          </p>
        </div>
        {ready && !singleDay && (
          <div className="flex flex-wrap gap-5 sm:gap-8">
            <div>
              <p className="text-xs text-[var(--muted-foreground)]">В среднем за день</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">
                {number.format(total / days)}{" "}
                <span className="text-xs font-normal text-[var(--muted-foreground)]">меш.</span>
              </p>
            </div>
            <div>
              <p className="text-xs text-[var(--muted-foreground)]">Дней с выпуском</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">
                {daysWithBags} <span className="text-xs font-normal text-[var(--muted-foreground)]">из {days}</span>
              </p>
            </div>
          </div>
        )}
        <span role="status" className="inline-flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
          {loading ? (
            <RefreshCw className="size-3.5 animate-spin" />
          ) : (
            <span
              className={cn("size-1.5 rounded-full", ready ? "bg-[var(--success)]" : "bg-[var(--muted-foreground)]/40")}
            />
          )}
          {loading ? "Загружаем данные…" : ready ? "Обновляется автоматически" : "Итог пока недоступен"}
        </span>
      </Panel>

      {ready && total === 0 && !hasActivity ? (
        <Panel className="px-5 py-9 text-center">
          <CalendarDays className="mx-auto mb-3 size-6 text-[var(--muted-foreground)]" />
          <h3 className="font-medium">За этот период мешки не учтены</h3>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">Выберите другой день или расширьте период.</p>
        </Panel>
      ) : ready ? (
        <div className={cn("grid items-start gap-5", !singleDay && "lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]")}>
          {!singleDay && (
            <Panel className="min-w-0 p-5">
              <h3 className="text-base font-semibold">Учтено по дням</h3>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">Выберите день, чтобы посмотреть детали.</p>
              <div
                role="region"
                aria-label="График по дням"
                tabIndex={0}
                className="mt-5 overflow-x-auto rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                <div
                  className="flex h-48 items-end gap-1.5 pb-1"
                  style={{ minWidth: Math.max(260, history.length * 42) }}
                >
                  {history.map((point) => {
                    const active = selectedDay === point.day;
                    return (
                      <button
                        key={point.day}
                        type="button"
                        aria-label={`Аналитика за ${fullDay(point.day)}: ${point.total} мешков`}
                        aria-pressed={active}
                        onClick={() => onSelectDay(active ? null : point.day)}
                        className="group flex h-full min-w-0 flex-1 flex-col items-center rounded-md px-1 pt-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                      >
                        <span
                          className={cn(
                            "mb-2 text-[10px] font-medium tabular-nums",
                            active ? "text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                          )}
                        >
                          {number.format(point.total)}
                        </span>
                        <span className="flex w-full flex-1 items-end justify-center">
                          <span
                            className={cn(
                              "w-full max-w-8 rounded-t-md transition-colors group-hover:bg-[var(--foreground)]/60",
                              active ? "bg-[var(--foreground)]" : "bg-[var(--muted-foreground)]/30",
                            )}
                            style={{ height: point.total ? `${Math.max(3, (point.total * 100) / chartMax)}%` : 2 }}
                          />
                        </span>
                        <span
                          className={cn(
                            "mt-2 text-[10px] tabular-nums",
                            active ? "font-semibold" : "text-[var(--muted-foreground)]",
                          )}
                        >
                          {shortDay(point.day)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </Panel>
          )}
          <Panel className="min-w-0 overflow-hidden p-5">
            <h3 className="text-base font-semibold">{isShipping ? "Цвета мешков" : "Продукция"}</h3>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">Количество и доля за выбранный период</p>
            {adjustment !== 0 && (
              <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                Итог учитывает корректировку {adjustment > 0 ? "+" : ""}
                {number.format(adjustment)} меш. Здесь показано количество по распознанным цветам.
              </p>
            )}
            <div className="mt-4 divide-y divide-[var(--border)]">
              {colors.map((item) => {
                const destination = !isShipping ? resolveAlwaysOnReceiptDestination(receiptMapping, item.color) : null;
                const hasProduct = destination?.state === "bound";
                return (
                  <div key={item.color} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex items-start gap-2.5">
                      <ColorDot className={cn("mt-1.5", colorMeta(item.color).dot)} />
                      <span className="min-w-0 flex-1 break-words text-sm font-medium leading-5">
                        {hasProduct ? destination.productLabel : colorMeta(item.color).label}
                      </span>
                      <div className="shrink-0 text-right">
                        <span className="text-sm font-semibold tabular-nums">{number.format(item.total)}</span>
                        <span className="ml-2 text-xs tabular-nums text-[var(--muted-foreground)]">
                          {number.format(item.percent)}%
                        </span>
                      </div>
                    </div>
                    <div className="ml-5 mt-2 h-1 overflow-hidden rounded-full bg-[var(--muted)]">
                      <div
                        className={cn("h-full rounded-full", colorMeta(item.color).bar)}
                        style={{ width: `${item.percent}%` }}
                      />
                    </div>
                    {destination && (
                      <AlwaysOnReceiptDestinationLabel
                        destination={destination}
                        colorLabel={hasProduct ? undefined : colorMeta(item.color).label}
                        showProduct={!hasProduct}
                        className="ml-5 mt-2"
                      />
                    )}
                  </div>
                );
              })}
              {!colors.length && (
                <p className="py-4 text-sm text-[var(--muted-foreground)]">
                  Цвета мешков за этот период не определены.
                </p>
              )}
            </div>
            {singleDay && dayPoint && (
              <Button
                variant="outline"
                className="mt-5 w-full justify-between"
                aria-pressed={selectedDay === dayPoint.day}
                aria-label={`${isShipping ? "Подробнее о дне" : "Выпуск по времени"}: ${fullDay(dayPoint.day)}, ${dayPoint.total} мешков`}
                onClick={() => onSelectDay(selectedDay === dayPoint.day ? null : dayPoint.day)}
              >
                {isShipping ? "Подробнее о дне" : "Выпуск по времени"}
                {selectedDay === dayPoint.day ? <ArrowDown className="size-4" /> : <ChevronRight className="size-4" />}
              </Button>
            )}
          </Panel>
        </div>
      ) : null}
    </div>
  );
}
