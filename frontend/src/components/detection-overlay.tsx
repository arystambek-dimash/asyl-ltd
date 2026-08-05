"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { AlwaysOnDetection } from "@/lib/types";

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
    const label = raw?.label ?? (typeof raw?.class_name === "string" ? raw.class_name : undefined);
    const confidence = Number(raw?.confidence);
    return [
      {
        x,
        y,
        w,
        h,
        label: String(label ?? ""),
        color: bagColor(label),
        counted: Boolean(raw?.counted),
        confidence: Number.isFinite(confidence) ? confidence : null,
      },
    ];
  });
}

/**
 * Область, которую реально занимает кадр внутри контейнера.
 *
 * Видео рисуется с `object-contain`: при несовпадении пропорций сверху/снизу
 * или по бокам остаются поля, и рамки, растянутые на весь контейнер, съехали
 * бы с мешков. Пропорции берём у самого элемента (`videoWidth/videoHeight`).
 */
function useVideoBox(container: HTMLElement | null) {
  const [box, setBox] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    if (!container) return;
    // Видео — сосед оверлея, а не его потомок: оба лежат в общем relative-боксе
    // карточки. Искать его внутри себя бессмысленно — так рамки не рисовались
    // вообще, хотя координаты приходили.
    const parent = container.parentElement;
    const video = parent?.querySelector("video");
    if (!parent || !video) return;

    const measure = () => {
      const { videoWidth, videoHeight } = video;
      const { clientWidth, clientHeight } = parent;
      if (!videoWidth || !videoHeight || !clientWidth || !clientHeight) return setBox(null);
      const scale = Math.min(clientWidth / videoWidth, clientHeight / videoHeight);
      const width = videoWidth * scale;
      const height = videoHeight * scale;
      setBox({
        left: (clientWidth - width) / 2,
        top: (clientHeight - height) / 2,
        width,
        height,
      });
    };

    measure();
    video.addEventListener("loadedmetadata", measure);
    video.addEventListener("resize", measure);
    // Наблюдаем за родителем: размеры самого оверлея мы же и задаём, и
    // подписка на него дала бы петлю измерение → resize → измерение.
    // ResizeObserver есть не везде — без него рамки просто не подстроятся
    // под смену размера окна, но отрисуются.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(parent);
    return () => {
      video.removeEventListener("loadedmetadata", measure);
      video.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [container]);

  return box;
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
  className,
}: {
  detections: AlwaysOnDetection[] | undefined;
  /** Размер кадра модели: нужен, когда сервис отдаёт рамки в пикселях. */
  frame?: { width?: number; height?: number } | null;
  className?: string;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const box = useVideoBox(container);
  const drawable = normalizeDetections(detections, frame);

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
          {drawable.map((box, index) => (
            <div
              key={`${box.label}-${index}-${box.x}-${box.y}`}
              className="absolute rounded-[3px] transition-[left,top,width,height] duration-150 ease-out"
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
          ))}
        </>
      )}
    </div>
  );
}
