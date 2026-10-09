import { Badge } from "@/components/ui/badge";
import { GOODS_RETURN_STATUS_TONE } from "@/lib/constants";
import type { GoodsReturnStatus } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

/** Статус приёмки возврата — во вкладке «Заказы → Возвраты» и у кладовщика. */
interface ReturnState {
  status: GoodsReturnStatus;
  status_label: string;
  accepted_by_name: string | null;
  accepted_at: string | null;
}

/** «Ждёт приёмки» / «Полностью возвращено» / «Частично возвращено» / «Отменён»: подпись — с сервера. */
export function GoodsReturnStatusBadge({ row, className }: { row: ReturnState; className?: string }) {
  return (
    <Badge tone={GOODS_RETURN_STATUS_TONE[row.status]} className={className}>
      {row.status_label}
    </Badge>
  );
}

/** Кто вывел возврат из «Ждёт приёмки»: «Принял Айдос · 09.10.2026, 15:10», у отменённого — «Отменил …». */
export function GoodsReturnAcceptedBy({ row, className }: { row: ReturnState; className?: string }) {
  if (!row.accepted_by_name || !row.accepted_at) return null;
  return (
    <div className={className}>
      {row.status === "cancelled" ? "Отменил" : "Принял"} {row.accepted_by_name} · {formatDateTime(row.accepted_at)}
    </div>
  );
}
