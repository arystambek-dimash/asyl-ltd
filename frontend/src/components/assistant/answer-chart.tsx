"use client";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { TREND_GRID_PROPS } from "@/components/charts/money-trend-chart";
import { CHART_TOOLTIP_STYLE } from "@/components/ui/chart-tooltip";
import { formatAssistantCell, type AssistantTable } from "@/lib/assistant";
import { useIsMobile } from "@/lib/use-media-query";
import { formatMoney } from "@/lib/utils";

// Длинные списки на графике не читаются — их показывает таблица.
const MAX_BARS = 25;
const BAR_ROW = 26;

const AXIS_TICK = { fontSize: 11, fill: "var(--muted-foreground)" } as const;
// На телефоне подписи полос уже: иначе на сами полосы не остаётся места.
const LABEL_WIDTH = { desktop: 150, mobile: 92 } as const;
const MOBILE_LABEL_CHARS = 13;

function shortLabel(label: string): string {
  return label.length > MOBILE_LABEL_CHARS ? `${label.slice(0, MOBILE_LABEL_CHARS - 1)}…` : label;
}

/**
 * График к таблице ответа: одна серия одним цветом — горизонтальные полосы для
 * сравнения (товары, клиенты), линия — по датам. Числа и подписи — из таблицы.
 */
export function AnswerChart({ table }: { table: AssistantTable & { chart: NonNullable<AssistantTable["chart"]> } }) {
  const mobile = useIsMobile();
  const { type, x, y } = table.chart;
  const xIndex = table.columns.indexOf(x);
  const yIndex = table.columns.indexOf(y);
  const rows = type === "bar" ? table.rows.slice(0, MAX_BARS) : table.rows;
  const data = rows.map((row) => ({ label: formatAssistantCell(row[xIndex] ?? null, x), value: Number(row[yIndex]) }));
  const tooltip = (
    <Tooltip
      contentStyle={CHART_TOOLTIP_STYLE}
      cursor={type === "bar" ? { fill: "var(--muted)" } : { stroke: "var(--border)" }}
      formatter={(value: number) => [formatMoney(value), y]}
    />
  );

  return (
    <div role="img" aria-label={`График: ${y} по ${x}, ${data.length} значений. Точные числа — в таблице.`}>
      {type === "bar" ? (
        <div style={{ height: data.length * BAR_ROW + 36 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
              <CartesianGrid {...TREND_GRID_PROPS} horizontal={false} vertical />
              <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} tickFormatter={formatMoney} />
              <YAxis
                type="category"
                dataKey="label"
                width={mobile ? LABEL_WIDTH.mobile : LABEL_WIDTH.desktop}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={false}
                tickFormatter={mobile ? shortLabel : undefined}
              />
              {tooltip}
              <Bar dataKey="value" fill="var(--ring)" radius={[0, 4, 4, 0]} barSize={14} isAnimationActive />
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="h-[240px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 12, right: 16, left: 4, bottom: 4 }}>
              <CartesianGrid {...TREND_GRID_PROPS} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} tickFormatter={formatMoney} />
              {tooltip}
              <Line
                type="linear"
                dataKey="value"
                stroke="var(--ring)"
                strokeWidth={2}
                dot={data.length <= 31 ? { r: 4, strokeWidth: 2, stroke: "var(--card)", fill: "var(--ring)" } : false}
                activeDot={{ r: 5 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {type === "bar" && table.rows.length > MAX_BARS && (
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          На графике первые {MAX_BARS} строк — все строки в таблице.
        </p>
      )}
    </div>
  );
}
