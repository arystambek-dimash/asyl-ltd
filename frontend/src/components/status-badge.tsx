import { Badge } from "@/components/ui/badge";
import { ORDER_STATUS_LABELS, ORDER_STATUS_TONE } from "@/lib/constants";

export function StatusBadge({ status, dot }: { status: string; dot?: boolean }) {
  return (
    <Badge tone={ORDER_STATUS_TONE[status] ?? "muted"} dot={dot}>
      {ORDER_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
