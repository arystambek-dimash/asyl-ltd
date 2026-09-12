import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { formatCurrency } from "@/lib/utils";

/** Разбивка итога по способам: «300 000 ₸ наличными · 400 000 ₸ QR». */
export function PaidMethodSummary({ summary }: { summary?: Record<string, Record<string, string>> }) {
  const parts = Object.entries(summary ?? {}).flatMap(([currency, methods]) =>
    Object.entries(methods).map(([method, amount]) => ({ currency, method, amount })),
  );
  // Один способ ничего не добавляет к уже показанному итогу.
  if (parts.length < 2) return null;
  return (
    // Сумма и способ не должны разъезжаться по строкам — переносим парами.
    <div className="mt-2 text-xs text-[var(--muted-foreground)]">
      {parts.map((part, index) => (
        <span key={`${part.currency}-${part.method}`} className="whitespace-nowrap">
          {index > 0 && <span className="px-1.5">·</span>}
          <span className="font-medium tabular-nums text-[var(--foreground)]">
            {formatCurrency(part.amount, part.currency)}
          </span>{" "}
          {PAYMENT_METHOD_LABELS[part.method] ?? part.method}
        </span>
      ))}
    </div>
  );
}
