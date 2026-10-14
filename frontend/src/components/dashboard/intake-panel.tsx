import { CircleHelp } from "lucide-react";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import { ProductPhoto } from "@/components/catalog/product-photo";
import { colorMeta, isUndeterminedColor } from "@/lib/monoblock-colors";
import { productParts, productTitle, twinKey, twinKeys } from "@/lib/product-parts";
import type { DashboardIntakeItem } from "@/lib/types";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatCount, formatTons } from "@/lib/utils";

/**
 * Подписи строк: товар — как на «Складе» (марка, сорт, фасовка; цвет только у
 * двойников), цвет без товара — цветом мешка.
 */
function itemTitles(items: DashboardIntakeItem[]): string[] {
  const twins = twinKeys(
    items.flatMap((item) => (item.product === null ? [] : [productParts(item.name, item.weight_kg)])),
  );
  return items.map((item) => {
    if (item.product === null) {
      const label = colorMeta(item.color).label;
      return isUndeterminedColor(item.color) ? label : `${label} — товар не выбран`;
    }
    const parts = productParts(item.name, item.weight_kg);
    return productTitle(parts, twins.has(twinKey(parts)) ? item.color_label : undefined);
  });
}

/** «Приход сегодня»: мешки, которые насчитали камеры AI 24/7 на линии (Робот Кука), по товарам. */
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
            <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5">
              <span className={cn(s.value, "font-semibold tracking-tight tabular-nums")}>
                {formatCount(intake.total)}
              </span>
              <span className={cn(s.unit, "text-[var(--muted-foreground)]")}>меш.</span>
              <span className={cn(s.unit, "text-[var(--muted-foreground)]")}>· {formatTons(intake.kg)} т</span>
            </div>
            <div className="mt-2 min-h-0 flex-1 overflow-hidden">
              {intake.cameras.map((camera) => {
                const titles = itemTitles(camera.items);
                return (
                  <div key={camera.camera} className="min-w-0">
                    <div className={cn(s.row, "flex min-w-0 items-baseline justify-between gap-3 border-b py-1.5")}>
                      <span className="min-w-0 truncate font-medium">{camera.name}</span>
                      <span className="shrink-0 font-semibold tabular-nums">{formatCount(camera.total)}</span>
                    </div>
                    <ul className="divide-y">
                      {camera.items.map((item, index) => (
                        <li
                          key={item.product ?? `color:${item.color}`}
                          className={cn(s.row, s.rowGap, "flex min-w-0 items-center gap-3")}
                        >
                          <ProductPhoto
                            url={item.product === null ? null : item.photo_url}
                            alt={titles[index]}
                            fallbackIcon={CircleHelp}
                            className={cn(s.thumb, "shrink-0")}
                            iconClassName={s.thumbIcon}
                          />
                          <span className="min-w-0 flex-1 truncate">{titles[index]}</span>
                          <span className="shrink-0 font-semibold tabular-nums">{formatCount(item.bags)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </>
        )
      )}
    </section>
  );
}
