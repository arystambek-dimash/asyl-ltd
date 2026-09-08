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
  wagon_id: number | null;
  photo_url: string | null;
  photo_status: string;
};

export function PassageHistory() {
  const [open, setOpen] = useState(false);
  const [before, setBefore] = useState<number | null>(null);
  const { data, loading, error, reload } = useApi<{ results: Capture[]; next_cursor: number | null }>(
    open ? `/grain/automatic-passage-scale/history/${before ? `?before=${before}` : ""}` : null,
  );
  useVisiblePolling(reload, 5000, open && before === null);
  return (
    <section className="rounded-xl border bg-[var(--card)] p-3">
      <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>
        Журнал взвешиваний
      </Button>
      {open && (
        <div className="mt-2 space-y-3">
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
                ? "Обработано оператором"
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
                      {row.reason ? ` · ${weighingReasonLabel(row.reason, row.detail)}` : ""}
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
      )}
    </section>
  );
}
