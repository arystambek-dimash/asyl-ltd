import { Badge } from "@/components/ui/badge";
import { ORDER_STATUS_LABELS, ORDER_STATUS_TONE } from "@/lib/constants";

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge tone={ORDER_STATUS_TONE[status] ?? "muted"}>
      {ORDER_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
