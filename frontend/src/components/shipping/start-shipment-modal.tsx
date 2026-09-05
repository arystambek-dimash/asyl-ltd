"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { Play } from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import type { ShippingActionResult } from "@/components/shipping/use-shipping-actions";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { PlateBadge } from "@/components/ui/license-plate-input";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { orderedBagCount } from "@/lib/orders";
import {
  availableCamerasForOrder,
  cameraPlaceholder,
  isCameraReady,
  type CameraAvailabilityContext,
  type PlayableCamera,
} from "@/lib/shipping-cameras";
import type { Order } from "@/lib/types";

const CONTINUOUS_PENDING_TEXT =
  "Камера-ПК ещё не подтвердила непрерывный процессор camN/sub. Запуск временно недоступен.";

export interface StartShipmentModalProps {
  /** Заказ, для которого запускается AI-подсчёт; null — модалка закрыта. */
  order: Order | null;
  /** Камеры моноблока, разрешённые настройкой «Камеры моноблока». */
  cameras: PlayableCamera[];
  /** Все играбельные камеры — для зоны закреплённой камеры вне списка моноблока. */
  camerasBySrc: Map<string, CameraFeed>;
  availability: CameraAvailabilityContext;
  /** Пояснение ПК камер, почему непрерывный контур не готов. */
  continuousDetail?: string;
  /** Киоск или `cameraSettings.locked`: камера не выбирается, а закреплена. */
  cameraLocked?: boolean;
  /** `me.monoblock_camera` киоска; иначе закреплённой считается первая камера. */
  kioskCamera?: string | null;
  onClose: () => void;
  /** Ошибка результата остаётся в модалке; при успехе модалка закрывается сама. */
  onStart: (order: Order, cameraSrc: string) => Promise<ShippingActionResult>;
}

/** «Начать погрузку?» — выбор камеры и запуск AI-подсчёта для заказа. */
export function StartShipmentModal({
  order,
  cameras,
  camerasBySrc,
  availability,
  continuousDetail = "",
  cameraLocked = false,
  kioskCamera = null,
  onClose,
  onStart,
}: StartShipmentModalProps) {
  const selectId = useId();
  const [selectedSrc, setSelectedSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Заказ в `loading` уже закреплён за камерой (перезапуск после «Выключить AI»);
  // киоск и закреплённая настройка тоже не выбирают камеру.
  const fixedSrc =
    order?.status === "loading" && order.loading_camera
      ? order.loading_camera
      : cameraLocked
        ? (kioskCamera ?? cameras[0]?.src ?? null)
        : null;
  const candidates = useMemo<PlayableCamera[]>(() => {
    if (!fixedSrc) return cameras;
    const fixed = cameras.find((camera) => camera.src === fixedSrc) ?? camerasBySrc.get(fixedSrc);
    return fixed?.src ? [fixed as PlayableCamera] : [];
  }, [cameras, camerasBySrc, fixedSrc]);
  const available = useMemo(
    () => availableCamerasForOrder(order, candidates, availability),
    [availability, candidates, order],
  );
  const cameraSrc = fixedSrc ? (available.some((camera) => camera.src === fixedSrc) ? fixedSrc : "") : selectedSrc;
  const placeholder = cameraPlaceholder(candidates, available, availability);
  const anyReady = candidates.some((camera) => isCameraReady(camera, availability));
  const fixedCamera = fixedSrc ? camerasBySrc.get(fixedSrc) : undefined;

  useEffect(() => {
    setSelectedSrc("");
    setError("");
    setBusy(false);
  }, [order?.id]);

  useEffect(() => {
    if (selectedSrc && !available.some((camera) => camera.src === selectedSrc)) setSelectedSrc("");
  }, [available, selectedSrc]);

  async function start() {
    if (!order || !cameraSrc) return;
    setBusy(true);
    setError("");
    const result = await onStart(order, cameraSrc);
    setBusy(false);
    if (result.ok) onClose();
    else setError(result.error);
  }

  const ordered = order ? orderedBagCount(order) : 0;

  return (
    <Modal
      open={!!order}
      onClose={() => !busy && onClose()}
      mobileFullscreen
      eyebrow={order ? `Заказ #${order.id}` : undefined}
      title="Начать погрузку?"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button disabled={busy || !cameraSrc} onClick={() => void start()}>
            <Play className="size-4" /> {busy ? "Запуск…" : "Начать погрузку"}
          </Button>
        </>
      }
    >
      {order && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            {order.truck_number ? (
              <PlateBadge value={order.truck_number} size="md" />
            ) : (
              <span className="text-[12px] text-[var(--muted-foreground)]">Без номера</span>
            )}
            <div className="min-w-0">
              <div className="truncate text-[14px] font-medium">{order.client_name || "Без клиента"}</div>
              <div className="text-[12px] tabular-nums text-[var(--muted-foreground)]">{ordered} меш. по заказу</div>
            </div>
          </div>

          {order.items.length > 0 && (
            <ul className="divide-y rounded-lg border text-[14px]">
              {order.items.map((item, index) => (
                <li key={item.id ?? `item-${index}`} className="flex items-center justify-between gap-3 px-3 py-2">
                  <span className="min-w-0 truncate">{item.product_label ?? "Товар"}</span>
                  <span className="shrink-0 tabular-nums text-[var(--muted-foreground)]">× {item.quantity}</span>
                </li>
              ))}
            </ul>
          )}

          {fixedSrc ? (
            <div className="rounded-lg border px-3 py-2.5 text-[14px]">
              <span className="text-[var(--muted-foreground)]">Камера: </span>
              <span className="font-medium">{fixedCamera?.zone || fixedCamera?.name || fixedSrc}</span>
              <span className="text-[var(--muted-foreground)]"> · закреплена</span>
              {!cameraSrc && <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">{placeholder}</p>}
            </div>
          ) : (
            <Field label="Камера" htmlFor={selectId}>
              <Select
                id={selectId}
                value={selectedSrc}
                disabled={!available.length}
                onChange={(event) => setSelectedSrc(event.target.value)}
              >
                <option value="">{placeholder}</option>
                {available.map((camera) => (
                  <option key={camera.id} value={camera.src}>
                    {camera.zone || camera.name} · AI-подсчёт
                  </option>
                ))}
              </Select>
            </Field>
          )}

          {!anyReady && (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {continuousDetail || CONTINUOUS_PENDING_TEXT}
            </p>
          )}

          {error && (
            <p
              role="alert"
              className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
            >
              {error}
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
