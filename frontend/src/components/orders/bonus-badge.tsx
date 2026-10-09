import { Badge } from "@/components/ui/badge";

/** Пометка бонусной позиции заказа: мешок грузится как обычный, но бесплатно. */
export function BonusBadge({ className }: { className?: string }) {
  return (
    <Badge tone="muted" className={className}>
      Бонус
    </Badge>
  );
}
