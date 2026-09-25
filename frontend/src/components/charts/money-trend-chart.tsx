"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { CHART_TOOLTIP_STYLE } from "@/components/ui/chart-tooltip";
import { formatCurrency } from "@/lib/utils";

/** Сетка дневных графиков: только горизонтальные пунктирные линии. */
export const TREND_GRID_PROPS = { strokeDasharray: "3 7", vertical: false, stroke: "var(--border)" } as const;

/** Ось дней: подпись точки из `label`, на длинном периоде — каждая четвёртая. */
export function trendXAxisProps(points: number) {
  return {
    dataKey: "label",
    tickLine: false,
    axisLine: false,
    tick: { fontSize: 11, fill: "var(--muted-foreground)" },
    interval: points > 14 ? 3 : 1,
    dy: 8,
  } as const;
}

interface MoneyTrendPoint {
  label: string;
  revenue: number;
  received: number;
}

const SERIES_LABEL = { revenue: "Выручка", received: "Поступило" } as const;

/**
 * Выручка (отгружено) и поступления (подтверждённые оплаты) по дням в одной валюте.
 * Точки уже приведены к `currency` адаптером отчёта — валюты здесь не смешиваются.
 */
export function MoneyTrendChart({
  data,
  currency,
  className,
  formatLabel = (label) => label,
}: {
  data: MoneyTrendPoint[];
  currency: string;
  /** Размер и отступы контейнера графика. */
  className: string;
  /** Подпись дня в подсказке и в списке для скринридера. */
  formatLabel?: (label: string) => string;
}) {
  return (
    <>
      <div className={className} role="img" aria-label={`График выручки и поступлений по дням, ${data.length} дней.`}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 18, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="money-trend-revenue-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.22} />
                <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid {...TREND_GRID_PROPS} />
            <XAxis {...trendXAxisProps(data.length)} />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              formatter={(value: number, name: string) => [
                formatCurrency(value, currency),
                name === "revenue" ? SERIES_LABEL.revenue : SERIES_LABEL.received,
              ]}
              labelFormatter={(label) => formatLabel(String(label))}
            />
            <Area
              type="monotone"
              dataKey="revenue"
              stroke="var(--ring)"
              strokeWidth={2.25}
              fill="url(#money-trend-revenue-fill)"
            />
            <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={2} fillOpacity={0} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <ul className="sr-only">
        {data.map((point, index) => (
          <li key={`${index}-${point.label}`}>
            {formatLabel(point.label)}: {SERIES_LABEL.revenue.toLowerCase()} {formatCurrency(point.revenue, currency)},{" "}
            {SERIES_LABEL.received.toLowerCase()} {formatCurrency(point.received, currency)}
          </li>
        ))}
      </ul>
    </>
  );
}
