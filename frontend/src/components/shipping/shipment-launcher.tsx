"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  Check,
  ChevronDown,
  ClipboardList,
  LoaderCircle,
  LockKeyhole,
  Play,
  Radio,
  ShieldCheck,
} from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import { apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

type PlayableCamera = CameraFeed & { src: string };

function SelectCard({
  kind,
  label,
  value,
  displayValue,
  placeholder,
  children,
  onChange,
}: {
  kind: "camera" | "order";
  label: string;
  value: string;
  displayValue?: string;
  placeholder: string;
  children: React.ReactNode;
  onChange: (value: string) => void;
}) {
  const Icon = kind === "camera" ? Camera : ClipboardList;
  return (
    <label className="group relative flex min-h-[104px] w-full cursor-pointer items-center gap-3 rounded-[18px] border bg-[var(--card)] px-4 py-4 shadow-card transition-colors hover:border-[var(--soft-blue-border)] hover:bg-[var(--soft-blue)]/35 focus-within:border-[var(--ring)] focus-within:ring-4 focus-within:ring-[color-mix(in_srgb,var(--ring)_16%,transparent)]">
      <span className="flex size-11 shrink-0 items-center justify-center rounded-xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] text-[var(--soft-blue-foreground)]">
        <Icon className="size-5" strokeWidth={1.9} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
          {label}
        </span>
        <span
          className={cn(
            "mt-1.5 block truncate text-[15px] font-semibold",
            value ? "text-[var(--foreground)]" : "font-medium text-[var(--muted-foreground)]",
          )}
        >
          {displayValue || placeholder}
        </span>
      </span>
      <ChevronDown className="size-4 shrink-0 text-[var(--muted-foreground)]" />
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="absolute inset-0 min-h-11 cursor-pointer opacity-0"
        aria-label={label}
      >
        <option value="">{placeholder}</option>
        {children}
      </select>
    </label>
  );
}

function AssignedCameraCard({ camera, available }: { camera: PlayableCamera | null; available: boolean }) {
  return (
    <div className="flex min-h-[104px] w-full items-center gap-3 rounded-[18px] border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] px-4 py-4">
      <span className="flex size-11 shrink-0 items-center justify-center rounded-xl border border-[var(--soft-blue-border)] bg-[var(--card)] text-[var(--soft-blue-foreground)]">
        <Camera className="size-5" strokeWidth={1.9} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
          Камера моноблока <LockKeyhole className="size-3" />
        </span>
        <span className="mt-1.5 block truncate text-[15px] font-semibold text-[var(--foreground)]">
          {camera?.zone ?? "Камера не зарегистрирована"}
        </span>
      </span>
      <span
        className={cn("size-2.5 shrink-0 rounded-full", available ? "bg-[var(--success)]" : "bg-[var(--warning)]")}
      />
    </div>
  );
}

export function ShipmentLauncher({
  orders,
  cameras,
  busyCameras = [],
  cameraOwners = {},
  activeSessionCount = 0,
  cameraLocked = false,
  onStart,
  className,
}: {
  orders: Order[];
  cameras: PlayableCamera[];
  busyCameras?: string[];
  cameraOwners?: Record<string, number>;
  activeSessionCount?: number;
  cameraLocked?: boolean;
  onStart: (order: Order, camera: PlayableCamera) => Promise<void>;
  className?: string;
}) {
  const [orderId, setOrderId] = useState("");
  const [cameraSrc, setCameraSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const order = orders.find((item) => String(item.id) === orderId) ?? null;
  const availableCameras = useMemo(
    () =>
      cameras.filter((camera) => {
        const ownerId = cameraOwners[camera.src];
        if (ownerId != null) return ownerId === order?.id;
        return !busyCameras.includes(camera.src);
      }),
    [busyCameras, cameraOwners, cameras, order?.id],
  );
  const camera = availableCameras.find((item) => item.src === cameraSrc) ?? null;
  const equipmentOnline = cameras.some((item) => item.online);

  useEffect(() => {
    if (cameraSrc && !availableCameras.some((item) => item.src === cameraSrc)) {
      setCameraSrc("");
    }
  }, [availableCameras, cameraSrc]);

  useEffect(() => {
    if (!cameraLocked) return;
    const assigned = availableCameras[0];
    setCameraSrc(assigned?.src ?? "");
  }, [availableCameras, cameraLocked]);

  async function start() {
    if (!order || !camera) return;
    setBusy(true);
    setError("");
    try {
      await onStart(order, camera);
      setOrderId("");
      setCameraSrc(cameraLocked ? camera.src : "");
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={cn("overflow-hidden rounded-[22px] border bg-[var(--card)] shadow-card", className)}>
      <div className="flex flex-col gap-4 border-b px-5 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-start gap-3">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-[var(--foreground)] text-[var(--background)]">
            <Play className="size-5" />
          </span>
          <div>
            <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
              Пульт погрузочного поста
            </div>
            <h2 className="mt-1 text-xl font-bold tracking-tight text-[var(--foreground)]">Запустить отгрузку</h2>
            <p className="mt-1 max-w-2xl text-sm text-[var(--muted-foreground)]">
              Свяжите ожидающий заказ с камерой. Пост зафиксирует сессию и начнёт подсчёт мешков.
            </p>
          </div>
        </div>

        <div
          className={cn(
            "flex min-h-11 shrink-0 items-center gap-2.5 self-start rounded-xl border px-3.5 text-sm font-semibold sm:self-center",
            equipmentOnline
              ? "border-[var(--soft-green-border)] bg-[var(--soft-green)] text-[var(--soft-green-foreground)]"
              : "border-[var(--soft-amber-border)] bg-[var(--soft-amber)] text-[var(--soft-amber-foreground)]",
          )}
          role="status"
        >
          <span className="relative flex size-2.5">
            {equipmentOnline && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[var(--success)] opacity-35" />
            )}
            <span
              className={cn(
                "relative size-2.5 rounded-full",
                equipmentOnline ? "bg-[var(--success)]" : "bg-[var(--warning)]",
              )}
            />
          </span>
          Оборудование: {equipmentOnline ? "онлайн" : "нет связи"}
        </div>
      </div>

      <div className="bg-[var(--muted)]/35 p-4 sm:p-6">
        <div className="grid items-stretch gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_220px]">
          <SelectCard
            kind="order"
            label="1 · Заказ"
            value={orderId}
            displayValue={order ? `#${order.id} · ${order.client_name || "Без клиента"}` : undefined}
            placeholder={orders.length ? "Выберите заказ" : "Нет заказов в ожидании въезда"}
            onChange={setOrderId}
          >
            {orders.map((item) => (
              <option key={item.id} value={item.id}>
                #{item.id} · {item.client_name || "Без клиента"} · {orderedBagCount(item)} меш.
              </option>
            ))}
          </SelectCard>

          <div>
            {cameraLocked ? (
              <AssignedCameraCard camera={camera} available={!!camera} />
            ) : (
              <SelectCard
                kind="camera"
                label="2 · Камера"
                value={cameraSrc}
                displayValue={camera?.zone}
                placeholder={availableCameras.length ? "Выберите камеру" : "Нет свободных камер"}
                onChange={setCameraSrc}
              >
                {availableCameras.map((item) => (
                  <option key={item.id} value={item.src}>
                    {item.zone}
                  </option>
                ))}
              </SelectCard>
            )}
          </div>

          <button
            type="button"
            onClick={start}
            disabled={!order || !camera || busy}
            className="group flex min-h-[104px] items-center justify-center gap-3 rounded-[18px] bg-[var(--foreground)] px-5 text-[var(--background)] shadow-card transition-[transform,box-shadow,opacity] hover:-translate-y-0.5 hover:shadow-float active:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-[var(--background)]/12">
              {busy ? (
                <LoaderCircle className="size-5 animate-spin" />
              ) : (
                <Play className="size-5 transition-transform group-hover:translate-x-0.5" />
              )}
            </span>
            <span className="text-left">
              <span className="block text-[11px] font-bold uppercase tracking-[0.12em] opacity-65">3 · Запуск</span>
              <span className="mt-1 block text-base font-bold">{busy ? "Запускаем…" : "Начать отгрузку"}</span>
              {activeSessionCount > 0 && (
                <span className="mt-1 flex items-center gap-1.5 text-xs opacity-70">
                  <Radio className="size-3" /> {activeSessionCount} активн.
                </span>
              )}
            </span>
          </button>
        </div>

        <div className="mt-4 flex flex-col gap-3 rounded-2xl border bg-[var(--card)] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2.5 text-sm text-[var(--muted-foreground)]">
            <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[var(--success)]" />
            <span>
              {cameraLocked
                ? "Камера моноблока назначается автоматически. Перед запуском проверьте выбранный заказ."
                : "Одна камера обслуживает одну сессию. Занятые камеры скрыты из списка автоматически."}
            </span>
          </div>
          <span className="flex shrink-0 items-center gap-1.5 text-xs font-semibold text-[var(--muted-foreground)]">
            <Check className="size-3.5 text-[var(--success)]" />
            {cameraLocked ? "Закреплённая камера" : "Свободные камеры синхронизированы"}
          </span>
        </div>

        {error && (
          <div
            role="alert"
            className="mt-3 rounded-xl border border-[var(--soft-red-border)] bg-[var(--soft-red)] px-4 py-3 text-sm font-medium text-[var(--soft-red-foreground)]"
          >
            {error}
          </div>
        )}
      </div>
    </section>
  );
}
