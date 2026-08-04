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

export function bagColor(label: string): string {
  return BAG_COLORS[label.split("_")[0]] ?? FALLBACK_COLOR;
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
    const video = container.querySelector("video");
    if (!video) return;

    const measure = () => {
      const { videoWidth, videoHeight } = video;
      const { clientWidth, clientHeight } = container;
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
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => {
      video.removeEventListener("loadedmetadata", measure);
      video.removeEventListener("resize", measure);
      observer.disconnect();
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
  className,
}: {
  detections: AlwaysOnDetection[] | undefined;
  className?: string;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const box = useVideoBox(container);

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
      {!detections?.length ? null : (
        <>
          {detections.map((box, index) => {
            const color = bagColor(box.label);
            return (
              <div
                key={`${box.label}-${index}-${box.x}-${box.y}`}
                className="absolute rounded-[3px] transition-[left,top,width,height] duration-150 ease-out"
                style={{
                  left: `${box.x * 100}%`,
                  top: `${box.y * 100}%`,
                  width: `${box.w * 100}%`,
                  height: `${box.h * 100}%`,
                  borderColor: color,
                  // Засчитанный мешок выделяется толщиной и свечением — видно
                  // не только что модель его нашла, но и что счётчик его принял.
                  borderWidth: box.counted ? 3 : 1.5,
                  borderStyle: "solid",
                  boxShadow: box.counted ? `0 0 0 1px #fff, 0 0 12px ${color}` : undefined,
                }}
              >
                <span
                  className="absolute -top-[18px] left-0 whitespace-nowrap rounded-[3px] px-1 text-[10px] font-bold leading-4 text-white"
                  style={{ backgroundColor: color }}
                >
                  {box.counted && "✓ "}
                  {box.label} {Math.round(box.confidence * 100)}%
                </span>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
