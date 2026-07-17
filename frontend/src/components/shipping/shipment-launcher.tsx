"use client";

import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import {
  Camera,
  Check,
  ChevronDown,
  ClipboardList,
  LoaderCircle,
  Play,
  Radio,
} from "lucide-react";
import type { CameraFeed } from "@/components/camera-wall";
import { apiError } from "@/lib/api";
import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

type PlayableCamera = CameraFeed & { src: string };

function bagCount(order: Order) {
  return order.items.reduce((sum, item) => sum + Number(item.quantity), 0);
}

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
    <label className="group relative flex min-h-[86px] w-full cursor-pointer items-center gap-3 rounded-[20px] border border-[#dfe7f4] bg-white/95 px-4 py-3 shadow-[0_14px_42px_rgba(41,72,126,0.10)] backdrop-blur-xl transition hover:-translate-y-0.5 hover:border-[#bfd0ee] hover:shadow-[0_18px_48px_rgba(41,72,126,0.14)] focus-within:ring-4 focus-within:ring-blue-500/10 lg:max-w-[338px]">
      <span className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-[#f3f7ff] text-[#3f69dd] transition group-hover:bg-[#eaf1ff]">
        <Icon className="size-5" strokeWidth={1.9} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[12px] font-medium text-slate-400">{label}</span>
        <span className={cn(
          "mt-1 block truncate text-[15px] font-semibold",
          value ? "text-slate-800" : "font-medium text-slate-400",
        )}>
          {displayValue || placeholder}
        </span>
      </span>
      <ChevronDown className="size-4 shrink-0 text-slate-500" />
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="absolute inset-0 cursor-pointer opacity-0"
        aria-label={label}
      >
        <option value="">{placeholder}</option>
        {children}
      </select>
    </label>
  );
}

export function ShipmentLauncher({
  orders,
  cameras,
  busyCameras = [],
  cameraOwners = {},
  activeSessionCount = 0,
  onStart,
  className,
}: {
  orders: Order[];
  cameras: PlayableCamera[];
  busyCameras?: string[];
  cameraOwners?: Record<string, number>;
  activeSessionCount?: number;
  onStart: (order: Order, camera: PlayableCamera) => Promise<void>;
  className?: string;
}) {
  const [orderId, setOrderId] = useState("");
  const [cameraSrc, setCameraSrc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const order = orders.find((item) => String(item.id) === orderId) ?? null;
  const availableCameras = useMemo(
    () => cameras.filter((camera) => {
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

  async function start() {
    if (!order || !camera) return;
    setBusy(true);
    setError("");
    try {
      await onStart(order, camera);
      setOrderId("");
      setCameraSrc("");
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={cn(
      "shipping-console relative isolate min-h-[390px] overflow-hidden rounded-[28px] border border-[#d8e3f4] bg-[#f8fbff] shadow-[0_18px_54px_rgba(45,79,137,0.11)]",
      className,
    )}>
      <Image
        src="/shipping/dock-hero.jpg"
        alt="Складской пост отгрузки"
        fill
        priority
        sizes="(max-width: 1024px) 100vw, 1400px"
        className="-z-20 object-cover object-left"
      />
      <div className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,rgba(248,251,255,0.18)_0%,rgba(248,251,255,0.78)_37%,rgba(248,251,255,0.97)_62%,rgba(248,251,255,0.92)_100%)]" />
      <div className="shipping-orbit absolute left-1/2 top-[44%] -z-10 size-[360px] -translate-x-1/2 -translate-y-1/2 rounded-full border border-blue-200/25" />

      <div className="absolute right-5 top-5 flex items-center gap-3 rounded-2xl border border-[#dce5f2] bg-white/90 px-4 py-3 text-[13px] shadow-[0_8px_28px_rgba(47,75,123,0.08)] backdrop-blur-lg sm:right-7 sm:top-6">
        <span className="relative flex size-2.5">
          {equipmentOnline && <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-40" />}
          <span className={cn("relative size-2.5 rounded-full", equipmentOnline ? "bg-emerald-500" : "bg-amber-400")} />
        </span>
        <span className="font-semibold text-slate-700">Оборудование</span>
        <span className={equipmentOnline ? "text-emerald-600" : "text-amber-600"}>
          {equipmentOnline ? "Онлайн" : "Нет связи"}
        </span>
      </div>

      <div className="relative z-10 flex min-h-[390px] flex-col items-center justify-center px-5 pb-7 pt-24 sm:px-8 lg:px-10 lg:pb-6 lg:pt-12">
        <div className="grid w-full max-w-[1180px] items-center gap-5 lg:grid-cols-[minmax(230px,1fr)_260px_minmax(230px,1fr)] lg:gap-10">
          <div className="order-2 flex justify-center lg:order-1 lg:justify-end">
            <SelectCard
              kind="camera"
              label="Камера"
              value={cameraSrc}
              displayValue={camera?.zone}
              placeholder={availableCameras.length ? "Выберите камеру" : "Нет свободных камер"}
              onChange={setCameraSrc}
            >
              {availableCameras.map((item) => (
                <option key={item.id} value={item.src}>{item.zone}</option>
              ))}
            </SelectCard>
          </div>

          <div className="order-1 flex flex-col items-center lg:order-2">
            <button
              type="button"
              onClick={start}
              disabled={!order || !camera || busy}
              className="shipping-start group relative flex size-[214px] flex-col items-center justify-center rounded-full border-[5px] border-white bg-[radial-gradient(circle_at_36%_26%,#5d86f4_0%,#3564e7_45%,#2446cd_100%)] text-white shadow-[0_22px_60px_rgba(39,79,211,0.35),0_0_0_1px_rgba(54,102,226,0.18)] transition duration-300 hover:-translate-y-1 hover:shadow-[0_28px_70px_rgba(39,79,211,0.42),0_0_0_12px_rgba(65,112,232,0.06)] active:translate-y-0 disabled:cursor-not-allowed disabled:grayscale-[0.25] disabled:opacity-65 sm:size-[232px]"
            >
              {busy ? (
                <LoaderCircle className="mb-3 size-11 animate-spin" strokeWidth={2} />
              ) : (
                <Play className="mb-3 size-12 fill-none transition-transform group-hover:scale-110" strokeWidth={1.8} />
              )}
              <span className="text-center text-[20px] font-semibold leading-tight sm:text-[22px]">
                Начать<br />отгрузку
              </span>
              {activeSessionCount > 0 && (
                <span className="mt-2 flex items-center gap-1.5 text-[11px] text-blue-100">
                  <Radio className="size-3" /> {activeSessionCount} активн.
                </span>
              )}
            </button>
          </div>

          <div className="order-3 flex justify-center lg:justify-start">
            <SelectCard
              kind="order"
              label="Заказ"
              value={orderId}
              displayValue={order ? `#${order.id} · ${order.client_name || "Без клиента"}` : undefined}
              placeholder={orders.length ? "Выберите заказ" : "Нет заказов в ожидании въезда"}
              onChange={setOrderId}
            >
              {orders.map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id} · {item.client_name || "Без клиента"} · {bagCount(item)} меш.
                </option>
              ))}
            </SelectCard>
          </div>
        </div>

        <p className="mt-5 max-w-[570px] text-center text-[14px] font-medium leading-relaxed text-[#415174] sm:text-[15px]">
          Выберите ожидающий въезда заказ и свободную камеру — заказ перейдёт
          в «Загружается», и начнётся живое считывание мешков.
        </p>
        {error && <p className="mt-2 text-center text-sm font-medium text-red-600">{error}</p>}

        <div className="mt-4 flex flex-wrap justify-center gap-2 text-[11px] text-slate-500 lg:absolute lg:bottom-5 lg:right-6 lg:mt-0">
          <span className="flex items-center gap-1.5 rounded-full border border-white/80 bg-white/75 px-2.5 py-1 backdrop-blur">
            <Check className="size-3 text-emerald-600" /> одна камера — одна сессия
          </span>
        </div>
      </div>
    </section>
  );
}
