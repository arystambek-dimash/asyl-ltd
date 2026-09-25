import { vi } from "vitest";

const GEOMETRY = [
  [HTMLVideoElement.prototype, "videoWidth", 1920],
  [HTMLVideoElement.prototype, "videoHeight", 1080],
  [HTMLElement.prototype, "clientWidth", 800],
  [HTMLElement.prototype, "clientHeight", 600],
] as const;

const originals = GEOMETRY.map(([target, key]) => Object.getOwnPropertyDescriptor(target, key));

/**
 * jsdom не проигрывает видео и не раскладывает элементы, поэтому размеры кадра
 * и контейнера задаём вручную: кадр 1920×1080 в контейнере 800×600. Вписанное
 * (contain) видео занимает полосу 800×450 с отступом 75 px сверху.
 */
export function installVideoGeometry() {
  for (const [target, key, value] of GEOMETRY) Object.defineProperty(target, key, { configurable: true, value });
}

/** Возвращает прототипам свойства jsdom, подменённые installVideoGeometry(). */
export function restoreVideoGeometry() {
  GEOMETRY.forEach(([target, key], index) => {
    const original = originals[index];
    if (original) Object.defineProperty(target, key, original);
    else Reflect.deleteProperty(target, key);
  });
}

/** Экранный прямоугольник оверлея — полоса вписанного видео; захвата указателя в jsdom нет. */
export function stubOverlaySurface(overlay: HTMLElement) {
  overlay.getBoundingClientRect = () =>
    ({ left: 0, top: 75, width: 800, height: 450, right: 800, bottom: 525, x: 0, y: 75, toJSON() {} }) as DOMRect;
  overlay.setPointerCapture = vi.fn();
  overlay.hasPointerCapture = vi.fn(() => false);
  return overlay;
}
