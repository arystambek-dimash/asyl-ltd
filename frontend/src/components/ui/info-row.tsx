import type { ReactNode } from "react";

/** Строка карточки «подпись — значение» с разделителем; размер текста задаёт родитель. */
export function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-[var(--border)]/60 py-2 last:border-0">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}
