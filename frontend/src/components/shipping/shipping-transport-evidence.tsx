"use client";

import Image from "next/image";
import { Badge } from "@/components/ui/badge";
import { ErrorAlert } from "@/components/ui/data-state";
import { ShippingTransportTrackingDetails } from "@/components/shipping/shipping-transport-tracking";
import { ShippingAutoFinishDetails } from "@/components/shipping/shipping-auto-finish";
import type { ShippingTransportHistory } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";

const labels: Record<ShippingTransportHistory["status"], string> = {
  matched: "Заказ найден",
  no_order: "Без заказа",
  multiple_orders: "Несколько заказов",
  tracking_alert: "Проверьте транспорт",
  observed: "Номер зафиксирован",
};

type EvidenceScope = { orderId: number; conveyorCamera?: never } | { orderId?: never; conveyorCamera: string };

/** Mounted inside order details or the unresolved conveyor's expanded details. */
export function ShippingTransportEvidence({
  orderId,
  conveyorCamera,
  liveAutoFinish = false,
}: EvidenceScope & { liveAutoFinish?: boolean }) {
  const query = orderId != null ? `order_id=${orderId}` : `conveyor_camera=${encodeURIComponent(conveyorCamera)}`;
  const history = useApi<ShippingTransportHistory[]>(`/cameras/shipping-transport/history/?${query}`);
  useVisiblePolling(history.reload, liveAutoFinish ? 3_000 : 10_000);
  const autoFinish = history.data?.find((item) => item.auto_finish)?.auto_finish;

  return (
    <section className="w-full max-w-2xl space-y-3 rounded-lg border p-3" aria-label="Распознанный транспорт">
      <h4 className="text-sm font-medium">Распознанный транспорт</h4>
      <ShippingAutoFinishDetails value={autoFinish} historical={!liveAutoFinish} stale={!!history.error} />
      {history.error && <ErrorAlert message={history.error} onRetry={() => void history.reload()} />}
      {!history.data && history.loading && <p className="text-xs text-[var(--muted-foreground)]">Загрузка снимков…</p>}
      {history.data?.length === 0 && (
        <p className="text-xs text-[var(--muted-foreground)]">Распознанных номеров пока нет.</p>
      )}
      <div className="space-y-4">
        {history.data?.map((item) => (
          <article
            key={item.id}
            className="space-y-2"
            aria-label={item.number ? `Номер ${item.number}` : "Номер не распознан"}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold tabular-nums">
                {item.number
                  ? `${item.recognition_model === "wagon_number" ? "Вагон " : ""}${item.number}`
                  : "Номер не распознан"}
              </span>
              <Badge tone={item.status === "matched" ? "success" : item.status === "observed" ? "muted" : "warning"}>
                {labels[item.status]}
              </Badge>
            </div>
            <p className="text-xs">
              Конвейер {item.conveyor_camera} · камера номера {item.number_camera}
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Первое появление: {formatDateTime(item.first_seen_at)}
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Последнее появление: {formatDateTime(item.last_seen_at)}
            </p>
            {item.order_id != null && orderId == null && <p className="text-xs">Заказ #{item.order_id}</p>}
            <ShippingTransportTrackingDetails tracking={item.tracking} alert={item.tracking_alert} historical />
            {item.image_url ? (
              <a
                href={item.image_url}
                target="_blank"
                rel="noopener noreferrer"
                className="block rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                aria-label={item.number ? `Открыть кадр номера ${item.number}` : "Открыть кадр транспорта"}
              >
                <Image
                  src={item.image_url}
                  alt={`${item.number ? `Номер ${item.number}` : "Транспорт"}, камера ${item.number_camera}`}
                  width={640}
                  height={360}
                  unoptimized
                  className="aspect-video w-full rounded-md bg-[var(--muted)] object-contain"
                />
              </a>
            ) : (
              <p className="text-xs text-[var(--muted-foreground)]">Кадр недоступен</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
