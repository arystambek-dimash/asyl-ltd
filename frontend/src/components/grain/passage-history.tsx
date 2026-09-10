"use client";

import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { apiFileUrl, formatKg } from "@/lib/grain";
import { formatDateTime } from "@/lib/utils";
import { photoStatusLabel, weighingReasonLabel } from "@/lib/weighing-evidence";

type Capture = {
  id: number;
  occurred_at: string;
  weight_kg: number | null;
  observed_weight_kg: string | null;
  vehicle_number: string;
  status: string;
  action: string;
  reason: string;
  detail: string;
  resolved: boolean;
  resolution?: "automatic" | "manual" | "discarded" | "";
  wagon_id: number | null;
  photo_url: string | null;
  photo_status: string;
};

export function PassageHistory() {
  const [before, setBefore] = useState<number | null>(null);
  const { data, loading, error, reload } = useApi<{ results: Capture[]; next_cursor: number | null }>(
    `/grain/automatic-passage-scale/history/${before ? `?before=${before}` : ""}`,
  );
  useVisiblePolling(reload, 5000, before === null);
  return (
    <section
      id="passage-history"
      role="tabpanel"
      aria-labelledby="passage-history-tab"
      className="rounded-xl border bg-[var(--card)] p-4 sm:p-5"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Журнал взвешиваний</h2>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => void reload()}>
          Обновить журнал
        </Button>
      </div>
      <div className="space-y-3">
        <p className="text-xs text-[var(--muted-foreground)]">
          Автоматические попытки, включая вес без номера и ошибки до его сохранения.
        </p>
        {error ? (
          <p role="alert">
            Не удалось загрузить журнал.{" "}
            <Button variant="ghost" onClick={() => void reload()}>
              Повторить
            </Button>
          </p>
        ) : null}
        {loading && !data ? <p>Загрузка журнала…</p> : null}
        {data?.results.length === 0 ? <p>Попыток взвешивания пока нет.</p> : null}
        <ul className="divide-y">
          {(data?.results ?? []).map((row) => {
            const photo = apiFileUrl(row.photo_url);
            const status = row.resolved
              ? row.resolution === "automatic"
                ? "Оформлено автоматически"
                : row.resolution === "manual"
                  ? "Обработано оператором"
                  : row.resolution === "discarded"
                    ? "Отклонено"
                    : "Взвешивание обработано"
              : row.status === "processing"
                ? "Обрабатывается"
                : row.status === "failed"
                  ? "Вес не оформлен"
                  : row.action === "unassigned"
                    ? "Нужна привязка"
                    : row.action === "entry"
                      ? "Въезд записан"
                      : "Выезд записан";
            return (
              <li key={row.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                {photo ? (
                  <a href={photo} target="_blank" rel="noreferrer">
                    {/* eslint-disable-next-line @next/next/no-img-element -- private signed evidence URL */}
                    <img
                      src={photo}
                      alt={`Взвешивание ${row.id}`}
                      loading="lazy"
                      className="h-14 w-24 rounded object-cover"
                    />
                  </a>
                ) : (
                  <span className="w-24 text-xs text-[var(--muted-foreground)]">
                    {photoStatusLabel(row.photo_status)}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div>
                    {formatDateTime(row.occurred_at)} · {row.vehicle_number || "Без номера"} ·{" "}
                    <strong>{row.weight_kg == null ? "Вес не подтверждён" : formatKg(row.weight_kg)}</strong>
                  </div>
                  <div>
                    {status}
                    {row.reason && !row.resolved ? ` · ${weighingReasonLabel(row.reason, row.detail)}` : ""}
                  </div>
                </div>
                {row.wagon_id ? (
                  <Link className="underline" href={`/grain/wagons/${row.wagon_id}`}>
                    Открыть рейс
                  </Link>
                ) : null}
              </li>
            );
          })}
        </ul>
        <div className="flex gap-2">
          {before ? (
            <Button variant="outline" onClick={() => setBefore(null)}>
              Последние
            </Button>
          ) : null}
          {data?.next_cursor ? (
            <Button variant="outline" disabled={loading} onClick={() => setBefore(data.next_cursor)}>
              Более ранние
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
