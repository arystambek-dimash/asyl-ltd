import { Card } from "@/components/ui/card";

export function KpiCard({
  label, value, sub,
}: { label: string; value: string; sub?: string }) {
  return (
    <Card className="p-6">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
        {label}
      </div>
      <div className="mt-2 text-3xl font-bold tabular-nums tracking-tight">{value}</div>
      {sub && <div className="mt-1 text-xs text-[var(--muted-foreground)]">{sub}</div>}
    </Card>
  );
}
