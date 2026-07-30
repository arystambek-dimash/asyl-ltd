"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function StageCarousel({
  active,
  slides,
  slideKeys,
}: {
  active: number;
  slides: ReactNode[];
  slideKeys?: readonly string[];
}) {
  const panes = useRef<(HTMLDivElement | null)[]>([]);
  const [height, setHeight] = useState<number>();

  useEffect(() => {
    const pane = panes.current[active];
    if (!pane) return;
    const update = () => setHeight(pane.offsetHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(pane);
    return () => observer.disconnect();
  }, [active]);

  return (
    <div className="overflow-hidden transition-[height] duration-500 ease-in-out" style={{ height }}>
      <div
        className="flex items-start transition-transform duration-500 ease-in-out"
        style={{ transform: `translateX(-${active * 100}%)` }}
      >
        {slides.map((slide, index) => {
          const isActive = index === active;
          return (
            <div
              key={slideKeys?.[index] ?? index}
              ref={(element) => {
                panes.current[index] = element;
              }}
              aria-hidden={isActive ? undefined : true}
              inert={!isActive}
              className={cn("w-full shrink-0 pb-1", !isActive && "pointer-events-none")}
            >
              {slide}
            </div>
          );
        })}
      </div>
    </div>
  );
}
