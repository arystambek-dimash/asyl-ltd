import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import { colorMeta } from "@/lib/monoblock-colors";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCount } from "@/lib/utils";

/** «Приход сегодня»: мешки, которые насчитали камеры AI 24/7 на линии (Робот Кука). */
export function IntakePanel({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const s = BOARD_SIZE[size];
  const intake = m.intake;
  // Журнал камер не догнан — ноль был бы ложным: показываем, что данных нет.
  const unavailable = Boolean(m.intakeError) || (intake != null && intake.analytics_sync?.available !== true);

  return (
    <section className={cn("flex min-h-0 min-w-0 flex-col rounded-[18px] border bg-[var(--card)] shadow-card", s.pad)}>
      <div className="flex items-baseline justify-between gap-3 border-b pb-2">
        <h3 className={s.title}>Приход сегодня</h3>
        <span className={cn(s.note, "text-[var(--muted-foreground)]")}>камеры 24/7</span>
      </div>
      {intake == null && !m.intakeError ? (
        <div className="mt-3 h-16 animate-pulse rounded-xl bg-[var(--muted)]" aria-label="Загрузка прихода" />
      ) : unavailable ? (
        <div className="py-4">
          <p className={cn(s.row, "font-medium text-[var(--warning)]")}>Нет данных с камер</p>
          <p className={cn(s.note, "mt-1 text-[var(--muted-foreground)]")}>
            {m.intakeError || intake?.analytics_sync?.detail}
          </p>
        </div>
      ) : intake && intake.cameras.length === 0 ? (
        <p className={cn(s.row, "py-6 text-[var(--muted-foreground)]")}>Камеры 24/7 не выбраны</p>
      ) : (
        intake && (
          <>
            <div className="mt-2 flex items-baseline gap-1.5">
              <span className={cn(s.value, "font-semibold tracking-tight tabular-nums")}>
                {formatCount(intake.total)}
              </span>
              <span className={cn(s.unit, "text-[var(--muted-foreground)]")}>меш.</span>
            </div>
            <ul className="mt-2 min-h-0 flex-1 divide-y overflow-hidden">
              {intake.cameras.map((camera) => (
                <li key={camera.camera} className={cn(s.row, s.rowGap, "min-w-0")}>
                  <div className="flex min-w-0 items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate">{camera.name}</span>
                    <span className="shrink-0 font-semibold tabular-nums">{formatCount(camera.total)}</span>
                  </div>
                  <div className={cn(s.note, "mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[var(--muted-foreground)]")}>
                    {camera.colors
                      .filter((color) => color.total > 0)
                      .map((color) => {
                        const meta = colorMeta(color.color);
                        return (
                          <span key={color.color} className="inline-flex items-center gap-1.5">
                            <span className={cn("size-[0.7em] rounded-full", meta.dot)} />
                            {meta.label} {formatCount(color.total)}
                          </span>
                        );
                      })}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )
      )}
    </section>
  );
}
