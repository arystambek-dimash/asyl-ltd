"use client";

import { Camera } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFileUrl } from "@/lib/grain";
import type { GrainWagon } from "@/lib/types";

function PhotoTile({ label, url, hint }: { label: string; url: string | null | undefined; hint: string }) {
  const src = apiFileUrl(url);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
        {label}
      </figcaption>
      {src ? (
        <a href={src} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl border bg-black/5">
          {/* eslint-disable-next-line @next/next/no-img-element -- приватная подписанная ссылка бэкенда */}
          <img src={src} alt={`${label}: фото машины`} loading="lazy" className="aspect-video w-full object-cover" />
        </a>
      ) : (
        <div className="flex aspect-video w-full items-center justify-center rounded-xl border border-dashed text-center text-xs text-[var(--muted-foreground)]">
          {hint}
        </div>
      )}
    </figure>
  );
}

/** Кадры с камеры проходной в момент взвешивания: доказательство, что весили именно эту машину. */
export function WagonPhotos({ wagon }: { wagon: GrainWagon }) {
  if (wagon.direction !== "passage") return null;
  const hasAny = Boolean(wagon.entry_photo_url || wagon.exit_photo_url);
  const weighed = wagon.entry_weight_kg != null || wagon.exit_weight_kg != null;
  if (!hasAny && !weighed) return null;
  return (
    <Card>
      <CardHeader className="p-4 pb-2">
        <CardTitle className="flex items-center gap-2">
          <Camera className="size-4 text-[var(--muted-foreground)]" /> Фото машины
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3 p-4 pt-0 sm:grid-cols-2">
        <PhotoTile
          label="Въезд"
          url={wagon.entry_photo_url}
          hint={wagon.entry_weight_kg == null ? "появится после взвешивания пустой" : "кадр не сохранён"}
        />
        <PhotoTile
          label="Выезд"
          url={wagon.exit_photo_url}
          hint={wagon.exit_weight_kg == null ? "появится после взвешивания гружёной" : "кадр не сохранён"}
        />
      </CardContent>
    </Card>
  );
}
