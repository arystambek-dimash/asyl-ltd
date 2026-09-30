"use client";

import type { ReactNode } from "react";
import { Camera } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFileUrl } from "@/lib/api-file-url";
import type { GrainWagon } from "@/lib/types";
import { photoStatusLabel } from "@/lib/weighing-evidence";
import { formatDateTime } from "@/lib/utils";

function PhotoTile({
  label,
  url,
  hint,
  subject = "машины",
}: {
  label: string;
  url: string | null | undefined;
  hint?: string;
  subject?: string;
}) {
  const src = apiFileUrl(url);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </figcaption>
      {src ? (
        <a href={src} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border bg-black/5">
          {/* eslint-disable-next-line @next/next/no-img-element -- приватная подписанная ссылка бэкенда */}
          <img
            src={src}
            alt={`${label}: фото ${subject}`}
            loading="lazy"
            className="aspect-video w-full object-cover"
          />
        </a>
      ) : (
        <div className="flex aspect-video w-full items-center justify-center rounded-xl border border-dashed text-center text-xs text-[var(--muted-foreground)]">
          {hint}
        </div>
      )}
    </figure>
  );
}

function PhotoCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="flex items-center gap-2">
          <Camera className="size-4 text-[var(--muted-foreground)]" /> {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3 p-4 pt-0 sm:grid-cols-2">{children}</CardContent>
    </Card>
  );
}

/**
 * Кадры с камер рейса. Вывоз — проходная в момент взвешивания: доказательство,
 * что весили именно эту машину. Приход — кадр, на котором камера зафиксировала
 * прибытие вагона; карточка появляется, только если кадр сохранён.
 */
export function WagonPhotos({ wagon }: { wagon: GrainWagon }) {
  if (wagon.direction !== "passage") {
    if (!wagon.arrival_photo_url) return null;
    const takenAt = wagon.arrival_photo_taken_at;
    return (
      <PhotoCard title="Фото прибытия">
        <PhotoTile
          label={takenAt ? `Прибытие · ${formatDateTime(takenAt)}` : "Прибытие"}
          url={wagon.arrival_photo_url}
          subject="вагона"
        />
      </PhotoCard>
    );
  }
  const hasAny = Boolean(wagon.entry_photo_url || wagon.exit_photo_url);
  const weighed = wagon.entry_weight_kg != null || wagon.exit_weight_kg != null;
  const reference = wagon.weighings?.find((row) => row.kind === "gross" && row.source === "historical");
  if (!hasAny && !weighed) return null;
  return (
    <PhotoCard title="Фото машины">
      <PhotoTile
        label={
          reference
            ? `Сохранённая тара${reference.reference_record_source === "manual" ? " · ручной ввод" : ""} · ${formatDateTime(reference.reference_record_at || reference.created_at)}`
            : "Въезд"
        }
        url={wagon.entry_photo_url}
        hint={
          wagon.entry_weight_kg == null
            ? "появится после взвешивания пустой"
            : reference?.reference_record_source === "manual"
              ? "Сохранённая тара введена вручную без фото"
              : wagon.weighings?.some((row) => row.kind === "gross" && row.source === "manual")
                ? "Заезд внесён вручную без фото"
                : photoStatusLabel(wagon.weighings?.find((row) => row.kind === "gross")?.photo_status)
        }
      />
      <PhotoTile
        label="Выезд"
        url={wagon.exit_photo_url}
        hint={
          wagon.exit_weight_kg == null
            ? "появится после взвешивания гружёной"
            : wagon.weighings?.some((row) => row.kind === "tare" && row.source === "manual")
              ? "Выездной вес внесён вручную без фото"
              : photoStatusLabel(wagon.weighings?.find((row) => row.kind === "tare")?.photo_status)
        }
      />
    </PhotoCard>
  );
}
