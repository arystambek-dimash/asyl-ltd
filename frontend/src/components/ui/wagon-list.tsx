import { TrainFront } from "lucide-react";
import type { ShipmentWagon } from "@/lib/types";
import { cn } from "@/lib/utils";
import { wagonsHeadline, wagonTons } from "@/lib/wagons";

/**
 * Вагоны отгрузки по отчёту: «12 вагонов · ст. Раустан» и номера. ``table`` —
 * с товаром, мешками и весом каждого вагона (карточка заказа). Без
 * ``headline`` — только номера, когда заголовок уже виден рядом.
 */
export function WagonList({
  wagons,
  station = "",
  variant = "chips",
  headline = true,
  className,
}: {
  wagons: readonly ShipmentWagon[];
  station?: string;
  variant?: "chips" | "table";
  headline?: boolean;
  className?: string;
}) {
  if (wagons.length === 0) return null;
  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      {headline && (
        <div className="flex items-center gap-1.5 text-sm font-semibold">
          <TrainFront className="size-4 shrink-0" /> {wagonsHeadline(wagons.length, station)}
        </div>
      )}
      {variant === "chips" ? (
        <ul aria-label="Вагоны" className="flex min-w-0 flex-wrap gap-1">
          {wagons.map((wagon) => (
            <li
              key={wagon.number}
              title={`${wagon.product_label} · ${wagon.bags} меш. · ${wagonTons(wagon)} т`}
              className="rounded-md border border-[var(--border)] bg-[var(--card)] px-1.5 py-0.5 font-mono text-xs tabular-nums text-[var(--foreground)]"
            >
              {wagon.number}
            </li>
          ))}
        </ul>
      ) : (
        <div className="min-w-0 overflow-x-auto rounded-lg border">
          <table aria-label="Вагоны" className="w-full text-sm">
            <thead className="bg-[var(--muted)]/60 text-left text-xs text-[var(--muted-foreground)]">
              <tr>
                <th className="px-3 py-2 font-medium">Вагон</th>
                <th className="px-3 py-2 font-medium">Товар</th>
                <th className="px-3 py-2 text-right font-medium">Мешков</th>
                <th className="px-3 py-2 text-right font-medium">Тонн</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {wagons.map((wagon) => (
                <tr key={wagon.number}>
                  <td className="whitespace-nowrap px-3 py-1.5 font-mono tabular-nums">{wagon.number}</td>
                  <td className="min-w-40 px-3 py-1.5">{wagon.product_label}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{wagon.bags}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{wagonTons(wagon)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
