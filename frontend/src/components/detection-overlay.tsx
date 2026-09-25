"use client";

import { useEffect, useState } from "react";
import type { AlwaysOnDetection } from "@/lib/types";
import { useVideoBox, videoBoxStyle } from "@/lib/use-video-box";

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

// Рамка старше этого времени описывает уже уехавший мешок — гасим её, чтобы
// она не висела на пустом месте при обрыве связи или остановке модели.
const STALE_AFTER_MS = 2_500;

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
 * Привести рамки AI-сервиса к долям кадра.
 *
 * Сервис отдаёт рамку в пикселях кадра модели — `{bbox: [x1, y1, x2, y2],
 * class_name}`. Пиксели делим на размер кадра из `detection_frame`. Без него
 * масштаб неизвестен: нарисовать «на глаз» значило бы показать рамку не на
 * том мешке, поэтому такие записи отбрасываем.
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
  if (!(Number.isFinite(frameWidth) && Number.isFinite(frameHeight) && frameWidth > 0 && frameHeight > 0)) return [];

  return (detections ?? []).flatMap((raw) => {
    if (!Array.isArray(raw?.bbox)) return [];
    const [x1, y1, x2, y2] = raw.bbox.map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite)) return [];
    const visible = visibleBox(x1 / frameWidth, y1 / frameHeight, (x2 - x1) / frameWidth, (y2 - y1) / frameHeight);
    if (!visible) return [];
    const label = typeof raw.class_name === "string" ? raw.class_name : undefined;
    const confidence = Number(raw.confidence);
    return [
      {
        ...visible,
        label: label ?? "",
        color: bagColor(label),
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
function useStale(updatedAt: number | undefined): boolean {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!updatedAt) return;
    setNow(Date.now());
    const remaining = updatedAt + STALE_AFTER_MS - Date.now();
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, remaining));
    return () => clearTimeout(timer);
  }, [updatedAt]);

  if (!updatedAt) return false;
  return now - updatedAt >= STALE_AFTER_MS;
}

/**
 * Рамки распознанных мешков поверх видео.
 *
 * Всегда-включённые камеры отдают чистый поток без вжатых рамок, поэтому
 * их рисует браузер по координатам из статуса процессора. Пиксели кадра
 * модели переводятся в доли кадра, так что оверлей не зависит ни от
 * разрешения камеры, ни от размера карточки.
 *
 * Из-за опроса рамки отстают от картинки на доли секунды — это цена того,
 * что горячий путь видео и счётчик остаются нетронутыми.
 */
export function DetectionOverlay({
  detections,
  frame,
  updatedAt,
}: {
  detections: AlwaysOnDetection[] | undefined;
  /** Размер кадра модели: без него пиксельные рамки не во что масштабировать. */
  frame?: { width?: number; height?: number } | null;
  /** Когда пришёл этот список рамок (`Date.now()`); без него рамки не гаснут. */
  updatedAt?: number;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const box = useVideoBox(container);
  const stale = useStale(updatedAt);
  const drawable = stale ? [] : normalizeDetections(detections, frame);
  const labelOccurrences = new Map<string, number>();

  return (
    <div
      aria-hidden
      ref={setContainer}
      className="pointer-events-none absolute inset-0 overflow-hidden"
      style={videoBoxStyle(box)}
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
                  borderWidth: 1.5,
                  borderStyle: "solid",
                }}
              >
                <span
                  className="absolute -top-[18px] left-0 whitespace-nowrap rounded-[3px] px-1 text-[10px] font-bold leading-4 text-white"
                  style={{ backgroundColor: box.color }}
                >
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
