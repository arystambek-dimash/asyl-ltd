import { Badge } from "@/components/ui/badge";
import { orderStatusLabel, orderStatusTone } from "@/lib/constants";

export function StatusBadge({ status, dot }: { status: string; dot?: boolean }) {
  return (
    <Badge tone={orderStatusTone(status)} dot={dot}>
      {orderStatusLabel(status)}
    </Badge>
  );
}
