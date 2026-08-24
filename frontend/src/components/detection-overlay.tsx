"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { AlwaysOnDetection } from "@/lib/types";
import { useVideoBox } from "@/lib/use-video-box";

/**
 * Цвет рамки = цвет мешка, который распознала модель.
 *
 * Метка приходит как «Red_50» — цвет и вес мешка. Берём цвет: оператор
 * сразу видит, тем ли классом модель посчитала мешок.
 */
const BAG_COLORS: Record<string, string> = {
  Red: "#F04438",
  Green: "#17B26A",
  Blue: "#2E90FA",
  White: "#D0D5DD",
};

const FALLBACK_COLOR = "#F79009";

/**
 * Данные приходят от AI-сервиса на ПК цеха, а он обновляется вручную и может
 * быть сильно старее CRM. Считать поля гарантированными нельзя: отсутствующая
 * метка роняла всю страницу монитора на `label.split`, а не просто одну рамку.
 */
export function bagColor(label: string | undefined | null): string {
  return BAG_COLORS[String(label ?? "").split("_")[0]] ?? FALLBACK_COLOR;
}

/** Рамка, готовая к отрисовке: доли кадра плюс оформление. */
type DrawableBox = {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  color: string;
  counted: boolean;
  confidence: number | null;
};

function visibleBox(x: number, y: number, w: number, h: number) {
  if (w <= 0 || h <= 0) return null;
  if (x >= 0 && y >= 0 && x + w <= 1 && y + h <= 1) return { x, y, w, h };
  const left = Math.max(0, x);
  const top = Math.max(0, y);
  const right = Math.min(1, x + w);
  const bottom = Math.min(1, y + h);
  if (right <= left || bottom <= top) return null;
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/**
 * Привести рамки к долям кадра, поняв любой формат AI-сервиса.
 *
 * ПК цеха обновляется вручную и живёт своей версией, поэтому в ответе
 * встречаются оба вида:
 *
 * - нормализованный — `{x, y, w, h, label}`, доли кадра (0..1);
 * - пиксельный — `{bbox: [x1, y1, x2, y2], class_name}`, точки кадра.
 *
 * Пиксели делим на размер кадра из `detection_frame`. Без него масштаб
 * неизвестен: нарисовать «на глаз» значило бы показать рамку не на том
 * мешке, поэтому такие записи отбрасываем.
 *
 * Запись без пригодных координат тоже отбрасывается целиком — рамка на
 * `NaN%` уехала бы по экрану вместо того, чтобы просто не появиться.
 */
export function normalizeDetections(
  detections: AlwaysOnDetection[] | undefined,
  frame?: { width?: number; height?: number } | null,
): DrawableBox[] {
  const frameWidth = Number(frame?.width);
  const frameHeight = Number(frame?.height);
  const canScale = Number.isFinite(frameWidth) && Number.isFinite(frameHeight) && frameWidth > 0 && frameHeight > 0;

  return (detections ?? []).flatMap((item) => {
    const raw = item as AlwaysOnDetection & {
      bbox?: unknown;
      class_name?: unknown;
    };
    let coords: number[];

    if (Array.isArray(raw?.bbox)) {
      if (!canScale) return [];
      const [x1, y1, x2, y2] = raw.bbox.map(Number);
      if (![x1, y1, x2, y2].every(Number.isFinite)) return [];
      coords = [x1 / frameWidth, y1 / frameHeight, (x2 - x1) / frameWidth, (y2 - y1) / frameHeight];
    } else {
      coords = [raw?.x, raw?.y, raw?.w, raw?.h].map(Number);
    }

    if (!coords.every(Number.isFinite)) return [];
    const [x, y, w, h] = coords;
    const visible = visibleBox(x, y, w, h);
    if (!visible) return [];
    const label = raw?.label ?? (typeof raw?.class_name === "string" ? raw.class_name : undefined);
    const confidence = Number(raw?.confidence);
    return [
      {
        ...visible,
        label: String(label ?? ""),
        color: bagColor(label),
        counted: Boolean(raw?.counted),
        confidence: Number.isFinite(confidence) ? confidence : null,
      },
    ];
  });
}

/**
 * Устарел ли последний список рамок.
 *
 * Обрыв связи или остановка модели оставляли последнюю рамку висеть на пустом
 * месте: новых данных нет, а старые никто не убирает. Поэтому истечение
 * отсчитывается таймером, а не только приходом следующего ответа.
 */
function useStale(updatedAt: number | undefined, staleAfterMs: number | undefined): boolean {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!updatedAt || !staleAfterMs) return;
    setNow(Date.now());
    const remaining = updatedAt + staleAfterMs - Date.now();
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, remaining));
    return () => clearTimeout(timer);
  }, [updatedAt, staleAfterMs]);

  if (!updatedAt || !staleAfterMs) return false;
  return now - updatedAt >= staleAfterMs;
}

/**
 * Рамки распознанных мешков поверх видео.
 *
 * Всегда-включённые камеры отдают чистый поток без вжатых рамок, поэтому
 * их рисует браузер по координатам из статуса процессора. Координаты
 * приходят в долях кадра, так что оверлей не зависит ни от разрешения
 * камеры, ни от размера карточки.
 *
 * Из-за опроса рамки отстают от картинки на доли секунды — это цена того,
 * что горячий путь видео и счётчик остаются нетронутыми.
 */
export function DetectionOverlay({
  detections,
  frame,
  updatedAt,
  staleAfterMs,
  className,
}: {
  detections: AlwaysOnDetection[] | undefined;
  /** Размер кадра модели: нужен, когда сервис отдаёт рамки в пикселях. */
  frame?: { width?: number; height?: number } | null;
  /** Когда пришёл этот список рамок (`Date.now()`). */
  updatedAt?: number;
  /** Через сколько рамка считается устаревшей и гаснет. */
  staleAfterMs?: number;
  className?: string;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const box = useVideoBox(container);
  const stale = useStale(updatedAt, staleAfterMs);
  const drawable = stale ? [] : normalizeDetections(detections, frame);
  const labelOccurrences = new Map<string, number>();

  return (
    <div
      aria-hidden
      ref={setContainer}
      className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}
      style={
        box
          ? { left: box.left, top: box.top, width: box.width, height: box.height, right: "auto", bottom: "auto" }
          : undefined
      }
    >
      {!drawable.length ? null : (
        <>
          {drawable.map((box) => {
            // YOLO orders rows by confidence, so different classes can swap
            // positions between snapshots. Preserve their DOM nodes by label
            // instead of remounting them merely because the array reordered.
            const occurrence = labelOccurrences.get(box.label) ?? 0;
            labelOccurrences.set(box.label, occurrence + 1);
            return (
              <div
                key={`${box.label}-${occurrence}`}
                // A one-second tween made the overlay follow an already old
                // HTTP snapshot for another full second. Keep only a short
                // visual softening so the box reaches the freshest position
                // while it still corresponds to the visible moving bag.
                className="absolute rounded-[3px] transition-[left,top,width,height,opacity] duration-150 ease-linear"
                style={{
                  left: `${box.x * 100}%`,
                  top: `${box.y * 100}%`,
                  width: `${box.w * 100}%`,
                  height: `${box.h * 100}%`,
                  borderColor: box.color,
                  // Засчитанный мешок выделяется толщиной и свечением — видно
                  // не только что модель его нашла, но и что счётчик его принял.
                  borderWidth: box.counted ? 3 : 1.5,
                  borderStyle: "solid",
                  boxShadow: box.counted ? `0 0 0 1px #fff, 0 0 12px ${box.color}` : undefined,
                }}
              >
                <span
                  className="absolute -top-[18px] left-0 whitespace-nowrap rounded-[3px] px-1 text-[10px] font-bold leading-4 text-white"
                  style={{ backgroundColor: box.color }}
                >
                  {box.counted && "✓ "}
                  {box.label}
                  {box.confidence === null ? "" : ` ${Math.round(box.confidence * 100)}%`}
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
