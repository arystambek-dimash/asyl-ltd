import { formatCurrency } from "@/lib/utils";

/** Доля итога, внесённая одним способом оплаты. */
export interface PaidMethodPart {
  currency: string;
  method: string;
  /** Подпись способа с бэка (labels.py). */
  label: string;
  amount: number | string;
}

/** Части итога ленты: paid_by_method и подписи способов из того же ответа. */
export function summaryPaidParts(summary?: {
  paid_by_method: Record<string, Record<string, string>>;
  method_labels: Record<string, string>;
}): PaidMethodPart[] {
  return Object.entries(summary?.paid_by_method ?? {}).flatMap(([currency, methods]) =>
    Object.entries(methods).map(([method, amount]) => ({
      currency,
      method,
      label: summary?.method_labels[method] ?? method,
      amount,
    })),
  );
}

/**
 * Из чего сложилась оплата: «300 000 ₸ наличными · 400 000 ₸ QR».
 *
 * Итоговая сумма сама по себе не отвечает на вопрос кассира «чем платили»,
 * а при смешанной оплате это и есть главное, что нужно видеть сразу.
 * Один способ показывать не нужно — он уже подписан рядом с суммой.
 */
export function PaidMethodSummary({ parts, className = "" }: { parts: PaidMethodPart[]; className?: string }) {
  if (parts.length < 2) return null;
  return (
    // Сумма и её способ переносятся только вместе: «300 000 ₸» отдельно от
    // «Наличные» читается как другая величина. Перенос допустим лишь между
    // способами, поэтому разделитель живёт внутри своей пары.
    <div className={`text-[var(--muted-foreground)] ${className}`}>
      {parts.map((part, index) => (
        <span key={`${part.currency}-${part.method}`} className="whitespace-nowrap">
          {index > 0 && <span className="px-1.5">·</span>}
          <span className="font-medium tabular-nums text-[var(--foreground)]">
            {formatCurrency(part.amount, part.currency)}
          </span>{" "}
          {part.label}
        </span>
      ))}
    </div>
  );
}
