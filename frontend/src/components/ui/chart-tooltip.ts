/** Единый стиль подсказок recharts — тултип не умеет наши css-классы. */
export const CHART_TOOLTIP_STYLE = {
  borderRadius: 12,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--foreground)",
  fontSize: 12,
  padding: "8px 11px",
  boxShadow: "0 12px 32px rgba(0,0,0,0.12)",
} as const;
