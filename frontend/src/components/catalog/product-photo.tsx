"use client";
import { useEffect, useRef, useState } from "react";
import { ImagePlus, Package, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFileUrl } from "@/lib/api-file-url";
import { PRODUCT_PHOTO_ACCEPT } from "@/lib/product-photo";
import { cn } from "@/lib/utils";

/** Фото товара или спокойная заглушка, если фото нет или ссылка устарела. */
export function ProductPhoto({
  url,
  alt,
  className,
  iconClassName = "size-6",
}: {
  url: string | null | undefined;
  alt: string;
  className?: string;
  iconClassName?: string;
}) {
  const src = apiFileUrl(url);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (!src || failedSrc === src) {
    return (
      <div
        aria-hidden
        className={cn(
          "flex items-center justify-center bg-[var(--muted)] text-[var(--muted-foreground)]/60",
          className,
        )}
      >
        <Package className={iconClassName} />
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка бэкенда, next/image её не оптимизирует
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailedSrc(src)}
      className={cn("bg-[var(--muted)] object-cover", className)}
    />
  );
}

/** Выбор фото в форме товара: превью, замена и удаление до сохранения. */
export function ProductPhotoPicker({
  currentUrl,
  file,
  removed,
  onPick,
  onRemove,
  disabled,
}: {
  currentUrl: string | null | undefined;
  file: File | null;
  removed: boolean;
  onPick: (file: File) => void;
  onRemove: () => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const hasPhoto = Boolean(preview || (currentUrl && !removed));
  return (
    <div className="flex items-center gap-4">
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element -- локальный blob выбранного файла
        <img src={preview} alt="Новое фото товара" className="size-20 shrink-0 rounded-lg object-cover" />
      ) : (
        <ProductPhoto
          url={removed ? null : currentUrl}
          alt="Фото товара"
          className="size-20 shrink-0 rounded-lg"
          iconClassName="size-7"
        />
      )}
      <div className="flex flex-col items-start gap-1.5">
        <input
          ref={inputRef}
          type="file"
          accept={PRODUCT_PHOTO_ACCEPT}
          className="sr-only"
          aria-label="Фото товара"
          disabled={disabled}
          onChange={(event) => {
            const picked = event.target.files?.[0];
            if (picked) onPick(picked);
            event.target.value = "";
          }}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
          >
            <ImagePlus className="size-4" /> {hasPhoto ? "Заменить фото" : "Загрузить фото"}
          </Button>
          {hasPhoto && (
            <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={onRemove}>
              <Trash2 className="size-4" /> Убрать
            </Button>
          )}
        </div>
        <p className="text-xs text-[var(--muted-foreground)]">JPG, PNG или WEBP — клиенты увидят его в каталоге</p>
      </div>
    </div>
  );
}
