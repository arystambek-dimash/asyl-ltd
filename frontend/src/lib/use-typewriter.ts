"use client";
import { useEffect, useState } from "react";
import { usePrefersReducedMotion } from "@/lib/use-media-query";

/**
 * Текст, который «печатается» за ~1,5 с (только что пришедший ответ ассистента).
 * Без анимации — сразу целиком: enabled=false или «уменьшить движение» в системе.
 */
export function useTypewriter(text: string, enabled: boolean): string {
  const reduced = usePrefersReducedMotion();
  const animate = enabled && !reduced;
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (!animate) return;
    const duration = Math.min(1500, Math.max(300, text.length * 10));
    // Отсчёт — от первого кадра: часы кадров и performance.now() бывают разными.
    let started: number | null = null;
    let frame = 0;
    const tick = (now: number) => {
      started ??= now;
      const count = Math.min(text.length, Math.max(1, Math.ceil(((now - started) / duration) * text.length)));
      setShown(count);
      if (count < text.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [animate, text]);

  return animate ? text.slice(0, shown) : text;
}
