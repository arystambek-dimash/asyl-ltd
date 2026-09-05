"use client";

import { useEffect, useState } from "react";
import { Archive, CalendarDays, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { ShippingBoardSettings } from "@/lib/types";
import { cn } from "@/lib/utils";

/** «Отгруженные заказы» — сколько дней выехавшие остаются в очереди. */
export function CompletedOrdersSettingsModal({
  open,
  settings,
  onClose,
  onSaved,
}: {
  open: boolean;
  settings: ShippingBoardSettings | null;
  onClose: () => void;
  onSaved: () => Promise<unknown>;
}) {
  const [days, setDays] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) {
      setDays(settings?.completed_orders_days ?? 1);
      setError("");
    }
  }, [open, settings?.completed_orders_days]);

  async function save() {
    setBusy(true);
    setError("");
    try {
      await api.patch("/cameras/shipping-settings/", { completed_orders_days: days });
      await onSaved();
      onClose();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      mobileFullscreen
      eyebrow="Настройка администратора"
      title="Отгруженные заказы"
      description="Выберите, сколько дней отгруженные заказы остаются на живом посту."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy || days < 1 || days > 90} onClick={save}>
            <Check className="size-4" /> Сохранить
          </Button>
        </>
      }
    >
      <div className="rounded-2xl border bg-slate-50 p-4">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-blue-600 text-white">
            <CalendarDays className="size-5" />
          </span>
          <div>
            <div className="text-sm font-bold text-slate-800">Период на доске</div>
            <div className="text-xs text-slate-500">От 1 до 90 дней</div>
          </div>
          <div className="relative ml-auto">
            <input
              type="number"
              min={1}
              max={90}
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="h-12 w-24 rounded-xl border bg-white pr-9 text-center text-xl font-black tabular-nums outline-none focus:ring-2 focus:ring-blue-500"
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
              дн.
            </span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-5 gap-2">
          {[1, 3, 7, 14, 30].map((value) => (
            <button
              type="button"
              key={value}
              onClick={() => setDays(value)}
              className={cn(
                "rounded-lg border py-2 text-xs font-bold transition-colors",
                days === value
                  ? "border-blue-600 bg-blue-600 text-white"
                  : "bg-white text-slate-600 hover:border-blue-300",
              )}
            >
              {value === 1 ? "Сегодня" : value}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-start gap-3 rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
        <Archive className="mt-0.5 size-4 shrink-0" />
        Видео подсчёта хранится отдельно на компьютере камер {settings?.video_retention_days ?? 14} дней.
      </div>
      {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
    </Modal>
  );
}
