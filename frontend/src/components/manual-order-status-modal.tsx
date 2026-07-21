"use client";

import { useEffect, useState } from "react";
import { Ban, Check, LoaderCircle, PackageCheck } from "lucide-react";
import { api, apiError } from "@/lib/api";
import type { Order } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

export type ManualOrderTarget = "shipped" | "cancelled";

function orderedBags(order: Order) {
  return order.items.reduce((total, item) => total + Number(item.quantity), 0);
}

function suggestedBags(order: Order) {
  return ["arrived", "loading", "loaded"].includes(order.status)
    ? (order.bags_loaded ?? 0)
    : orderedBags(order);
}

export function ManualOrderStatusModal({ order, target, onClose, onChanged }: {
  order: Order | null;
  target: ManualOrderTarget | null;
  onClose: () => void;
  onChanged: (order: Order) => void | Promise<void>;
}) {
  const [bags, setBags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!order || !target) return;
    setBags(String(suggestedBags(order)));
    setError("");
  }, [order, target]);

  if (!order || !target) return null;
  const fallback = suggestedBags(order);
  const usesCurrentCount = ["arrived", "loading", "loaded"].includes(order.status);
  const parsed = bags.trim() === "" ? null : Number(bags);
  const validBags = parsed != null && Number.isInteger(parsed) && parsed >= 0;

  async function apply(status: ManualOrderTarget, bagsLoaded?: number) {
    setBusy(true);
    setError("");
    try {
      const body: { status: ManualOrderTarget; bags_loaded?: number } = { status };
      if (bagsLoaded != null) body.bags_loaded = bagsLoaded;
      const response = await api.post<{ order: Order }>(`/orders/${order!.id}/set-status/`, body);
      await onChanged(response.data.order);
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (target === "cancelled") {
    return (
      <Modal
        open
        onClose={onClose}
        eyebrow={`Заказ #${order.id}`}
        title="Отменить заказ?"
        description="Незавершённая погрузка, ручной счёт и привязка камеры будут очищены. Склад не списывается."
        footer={(
          <>
            <Button variant="ghost" disabled={busy} onClick={onClose}>Назад</Button>
            <Button variant="destructive" disabled={busy} onClick={() => void apply("cancelled")}>
              {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Ban className="size-4" />}
              Отменить заказ
            </Button>
          </>
        )}
      >
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm leading-relaxed text-red-800">
          Если камера сейчас считает этот заказ, сначала остановите отгрузку в «Моноблоке» или на посту.
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </Modal>
    );
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`Ручная отгрузка · заказ #${order.id}`}
      title="Сколько мешков отгружено?"
      description="Камера к заказу не назначается. Результат сохранится как ручной, склад и история обновятся штатно."
      className="max-w-xl"
      footer={(
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>Закрыть</Button>
          <Button
            disabled={busy || !validBags}
            onClick={() => validBags && void apply("shipped", parsed!)}
          >
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}
            Завершить · {validBags ? parsed : "—"} меш.
          </Button>
        </>
      )}
    >
      <div className="space-y-4">
        <label className="block">
          <span className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
            <PackageCheck className="size-4 text-blue-600" /> Фактически загружено
          </span>
          <div className="relative">
            <input
              autoFocus
              type="number"
              min="0"
              step="1"
              inputMode="numeric"
              value={bags}
              onChange={(event) => setBags(event.target.value)}
              className="h-20 w-full rounded-2xl border bg-slate-50 px-5 pr-24 text-right text-4xl font-black tabular-nums outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-500/10"
            />
            <span className="absolute right-5 top-1/2 -translate-y-1/2 text-sm font-semibold text-slate-400">мешков</span>
          </div>
        </label>

        <button
          type="button"
          disabled={busy}
          onClick={() => void apply("shipped")}
          className="flex w-full items-center justify-between rounded-xl border border-dashed border-slate-300 bg-white px-4 py-3 text-left transition hover:border-blue-300 hover:bg-blue-50/50 disabled:opacity-60"
        >
          <span>
            <span className="block text-sm font-semibold text-slate-800">Завершить без ручного подсчёта</span>
            <span className="mt-0.5 block text-xs text-slate-500">
              {usesCurrentCount ? "Будет сохранён текущий результат погрузки" : "Будет принято количество из заказа"}
            </span>
          </span>
          <b className="text-lg tabular-nums text-slate-700">{fallback}</b>
        </button>

        <button
          type="button"
          disabled={busy}
          onClick={() => void apply("cancelled")}
          className="flex w-full items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-left text-red-800 transition hover:bg-red-100 disabled:opacity-60"
        >
          <Ban className="size-5 shrink-0" />
          <span>
            <span className="block text-sm font-semibold">Отменить без загрузки</span>
            <span className="mt-0.5 block text-xs text-red-600">Заказ станет отменённым, склад не изменится</span>
          </span>
        </button>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </Modal>
  );
}
