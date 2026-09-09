"use client";

import Image from "next/image";
import { useState } from "react";
import { Printer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { shippingNumberSource, type ShippingSegmentDetail } from "@/lib/shipping-sessions";
import { useApi } from "@/lib/use-api";

// A document keeps the captured count unchanged until the operator explicitly
// refreshes it. Background polling would change the number as print opens.
const documentTime = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Asia/Almaty",
  dateStyle: "short",
  timeStyle: "medium",
});
function time(value: string) {
  return documentTime.format(new Date(value));
}

export function ShippingSegmentDocument({ segment }: { segment: ShippingSegmentDetail }) {
  const [photoState, setPhotoState] = useState<{ url: string; failed: boolean } | null>(null);
  const photoReady = !segment.photo_url || photoState?.url === segment.photo_url;
  const photoFailed = photoState?.url === segment.photo_url && photoState.failed;
  return (
    <>
      <div className="shipping-segment-print-controls mb-6 flex flex-wrap items-center justify-between gap-3">
        <a href="/monoblock" className="text-sm underline">
          К сессиям отгрузки
        </a>
        <Button disabled={!photoReady} onClick={() => window.print()}>
          <Printer className="size-4" />
          {photoReady ? "Печать накладной" : "Загружаем кадр…"}
        </Button>
      </div>
      <article
        className="shipping-segment-document space-y-6 rounded-xl border bg-white p-6 text-black"
        aria-label={`Накладная отрезка ${segment.id}`}
      >
        <header className="border-b border-black/20 pb-4">
          <h1 className="text-2xl font-bold">Накладная отрезка #{segment.id}</h1>
          <p className="mt-1 text-sm">Учёт отгруженных мешков · сессия #{segment.session_id}</p>
          {!segment.ended_at && (
            <p className="mt-3 rounded border border-amber-300 p-2 text-sm">
              Промежуточная накладная: отрезок ещё открыт. Количество зафиксировано на момент загрузки документа.
            </p>
          )}
        </header>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
          <div>
            <dt className="text-gray-600">
              {segment.recognition_model === "wagon_number" ? "Номер вагона" : "Номер машины"}
            </dt>
            <dd className="text-lg font-semibold">{segment.number || "Не определён"}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Источник номера</dt>
            <dd>{shippingNumberSource(segment.number_source)}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Конвейер</dt>
            <dd>{segment.camera}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Заказ</dt>
            <dd>{segment.order_id ? `#${segment.order_id}` : "Не привязан"}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Начало</dt>
            <dd>{time(segment.started_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Окончание</dt>
            <dd>{segment.ended_at ? time(segment.ended_at) : "Ещё не завершён"}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Последний мешок</dt>
            <dd>{time(segment.last_counted_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-600">Простой до закрытия</dt>
            <dd>{segment.idle_timeout_seconds / 60} мин.</dd>
          </div>
        </dl>
        <div className="flex items-center justify-between border-y border-black/20 py-4">
          <span className="font-medium">Количество в этом отрезке</span>
          <strong className="text-2xl tabular-nums">
            {new Intl.NumberFormat("ru-RU").format(segment.total_bags)} меш.
          </strong>
        </div>
        <figure className="space-y-2">
          {segment.photo_url && !photoFailed ? (
            <Image
              src={segment.photo_url}
              alt={`Кадр номера отрезка ${segment.id}`}
              width={960}
              height={540}
              unoptimized
              loading="eager"
              className="max-h-[100mm] w-full rounded object-contain"
              onLoad={() => setPhotoState({ url: segment.photo_url!, failed: false })}
              onError={() => setPhotoState({ url: segment.photo_url!, failed: true })}
            />
          ) : (
            <p className="rounded border border-dashed p-6 text-center text-sm">Кадр номера недоступен</p>
          )}
          <figcaption className="text-xs text-gray-600">
            {segment.photo_taken_at ? `Кадр номера: ${time(segment.photo_taken_at)}` : "Время кадра не указано"}
          </figcaption>
        </figure>
        <p className="text-xs text-gray-600">
          Время указано по Алматы (UTC+5). Документ фиксирует номер, период и количество мешков данного отрезка.
        </p>
      </article>
    </>
  );
}

export function ShippingSegmentPrintPage({ segmentId }: { segmentId: number }) {
  const valid = Number.isSafeInteger(segmentId) && segmentId > 0;
  const detail = useApi<ShippingSegmentDetail>(valid ? `/cameras/shipping-segments/${segmentId}/` : null);
  const error = detail.error || (detail.errorStatus ? "Накладная недоступна. Проверьте права доступа." : "");
  return (
    <main className="shipping-segment-print-page mx-auto max-w-4xl p-6">
      <style>{`
      @page { size: A4; margin: 12mm; }
      @media print {
        body { background: white !important; color: black !important; }
        body > :not(.shipping-segment-print-page) { display: none !important; }
        .shipping-segment-print-page { max-width: none; margin: 0; padding: 0; }
        .shipping-segment-print-controls { display: none !important; }
        .shipping-segment-document { border: 0; border-radius: 0; padding: 0; }
        .shipping-segment-document figure, .shipping-segment-document dl > div { break-inside: avoid; }
      }
    `}</style>
      {!valid ? (
        <ErrorAlert message="Неверный номер отрезка." />
      ) : error ? (
        <ErrorAlert message={error} onRetry={() => void detail.reload()} />
      ) : detail.data ? (
        <ShippingSegmentDocument key={detail.data.id} segment={detail.data} />
      ) : (
        <DataGate loading={detail.loading} />
      )}
    </main>
  );
}
