"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import type { ShippingTransportAutomation } from "@/lib/types";
import { ShippingTransportEvidence } from "@/components/shipping/shipping-transport-evidence";
import { ShippingTransportTrackingDetails } from "@/components/shipping/shipping-transport-tracking";
import { ShippingAutoFinishDetails } from "@/components/shipping/shipping-auto-finish";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";

const labels: Record<ShippingTransportAutomation["state"], string> = {
  waiting_number: "Ожидает номер",
  confirming: "Проверяет номер",
  no_order: "Заказ не найден",
  multiple_orders: "Несколько заказов",
  starting: "Привязывает заказ",
  loading: "Идёт погрузка",
  completed: "Погрузка завершена",
  busy: "Камера занята",
  error: "Ошибка распознавания",
};
/** Recognition and acquisition run on the server, independently of this panel. */
export function ShippingTransportStatus({
  enabled,
  camerasBySrc,
}: {
  enabled: boolean;
  camerasBySrc: Map<string, CameraFeed>;
}) {
  const status = useApi<ShippingTransportAutomation[]>(enabled ? "/cameras/shipping-transport/" : null);
  useVisiblePolling(status.reload, 3_000, enabled);
  if (!enabled) return null;

  function cameraName(source: string) {
    const camera = camerasBySrc.get(source);
    return camera?.zone || camera?.name || source;
  }

  return (
    <section className="space-y-3" aria-label="Автоматическая привязка заказов">
      <div>
        <h3 className="text-sm font-semibold">Заказы по номеру транспорта</h3>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          Камеры работают 24/7. Заказ подключается к подсчёту после распознавания номера машины или вагона. Номер и кадр
          сохраняются в сведениях о погрузке.
        </p>
      </div>
      {status.error && <ErrorAlert message={status.error} onRetry={() => void status.reload()} />}
      {!status.data && status.loading && <p className="text-sm text-[var(--muted-foreground)]">Загрузка состояния…</p>}
      {status.data?.length === 0 && (
        <p className="text-sm text-[var(--muted-foreground)]">Настройте камеру номера во вкладке камеры отгрузки.</p>
      )}
      <div className="grid items-start gap-3 md:grid-cols-2 xl:grid-cols-3">
        {status.data?.map((item) => {
          const tone =
            item.state === "loading" || item.state === "completed"
              ? "success"
              : item.state === "error"
                ? "destructive"
                : ["no_order", "multiple_orders", "busy"].includes(item.state)
                  ? "warning"
                  : "muted";
          return (
            <div
              key={item.conveyor_camera}
              className="space-y-2 rounded-lg border p-4"
              role="group"
              aria-label={`Привязка: ${cameraName(item.conveyor_camera)}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">{cameraName(item.conveyor_camera)}</span>
                <Badge tone={tone} dot>
                  {labels[item.state]}
                </Badge>
              </div>
              {item.number && (
                <p className="font-semibold tabular-nums">
                  {item.recognition_model === "wagon_number" ? "Вагон " : ""}
                  {item.number}
                  {item.order_id != null && <span className="ml-2 text-sm font-normal">· заказ #{item.order_id}</span>}
                </p>
              )}
              {item.detail && <p className="text-xs text-[var(--muted-foreground)]">{item.detail}</p>}
              <ShippingTransportTrackingDetails
                tracking={item.tracking}
                alert={item.tracking_alert}
                stale={!!status.error}
              />
              <ShippingAutoFinishDetails value={item.auto_finish} stale={!!status.error} />
              {item.observed_at && (
                <p className="text-xs text-[var(--muted-foreground)]">
                  Номер замечен {formatDateTime(item.observed_at)}
                </p>
              )}
              {item.order_id == null && item.number_camera != null && (
                <ConveyorEvidence source={item.conveyor_camera} />
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ConveyorEvidence({ source }: { source: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Button
        variant="ghost"
        size="sm"
        aria-expanded={open}
        aria-controls={`transport-evidence-${source}`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        Сведения о транспорте
      </Button>
      {open && (
        <div id={`transport-evidence-${source}`} className="mt-2">
          <ShippingTransportEvidence conveyorCamera={source} />
        </div>
      )}
    </div>
  );
}
