import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Пометка пробной партии: заказ обычный, только помечен «Проба».
 * lg — у грузчика рядом с крупным именем клиента, видно издалека.
 */
export function TrialBadge({ size = "md", className }: { size?: "md" | "lg"; className?: string }) {
  return (
    <Badge tone="warning" className={cn(size === "lg" && "h-7 px-2.5 text-sm font-bold", className)}>
      Проба
    </Badge>
  );
}
