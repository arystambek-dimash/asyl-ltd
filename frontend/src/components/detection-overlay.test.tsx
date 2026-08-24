import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DetectionOverlay, bagColor, normalizeDetections } from "./detection-overlay";
import type { AlwaysOnDetection } from "@/lib/types";

function box(overrides: Partial<AlwaysOnDetection> = {}): AlwaysOnDetection {
  return {
    x: 0.1,
    y: 0.2,
    w: 0.3,
    h: 0.4,
    label: "Red_50",
    confidence: 0.91,
    counted: false,
    ...overrides,
  };
}

describe("bagColor", () => {
  it("colours the frame by the bag colour the model recognised", () => {
    expect(bagColor("Red_50")).toBe("#F04438");
    expect(bagColor("Green_25")).toBe("#17B26A");
    expect(bagColor("Blue_50")).toBe("#2E90FA");
  });

  it("falls back to a distinct colour for an unknown class", () => {
    // Новый класс в модели не должен молча слиться с существующим цветом.
    expect(bagColor("Yellow_10")).toBe("#F79009");
    expect(bagColor("странное")).toBe("#F79009");
  });
});

describe("DetectionOverlay", () => {
  it("places a box using fractions of the frame", () => {
    render(<DetectionOverlay detections={[box()]} />);

    const drawn = screen.getByText(/Red_50/).parentElement!;
    expect(drawn.style.left).toBe("10%");
    expect(drawn.style.top).toBe("20%");
    expect(drawn.style.width).toBe("30%");
    expect(drawn.style.height).toBe("40%");
  });

  it("shows the confidence the model reported", () => {
    render(<DetectionOverlay detections={[box({ confidence: 0.91 })]} />);

    expect(screen.getByText(/91%/)).toBeInTheDocument();
  });

  it("marks a counted bag apart from one that is merely seen", () => {
    render(
      <DetectionOverlay
        detections={[box({ counted: true, label: "Red_50" }), box({ counted: false, label: "Blue_50" })]}
      />,
    );

    const counted = screen.getByText(/✓ Red_50/).parentElement!;
    const seen = screen.getByText(/Blue_50/).parentElement!;
    // Засчитанный выделен толщиной — по нему видно работу счётчика.
    expect(counted.style.borderWidth).toBe("3px");
    expect(seen.style.borderWidth).toBe("1.5px");
  });

  it("keeps each box mounted when the processor changes detection order", () => {
    const { rerender } = render(
      <DetectionOverlay detections={[box({ label: "Red_50" }), box({ label: "Blue_50", x: 0.6 })]} />,
    );
    const redBefore = screen.getByText(/Red_50/).parentElement;
    const blueBefore = screen.getByText(/Blue_50/).parentElement;

    // Model output is confidence-ordered, so two otherwise continuous tracks
    // can swap rows between polls. Remounting here defeats the CSS transition
    // and makes both boxes visibly jump instead of moving smoothly.
    rerender(<DetectionOverlay detections={[box({ label: "Blue_50", x: 0.62 }), box({ label: "Red_50", x: 0.12 })]} />);

    expect(screen.getByText(/Red_50/).parentElement).toBe(redBefore);
    expect(screen.getByText(/Blue_50/).parentElement).toBe(blueBefore);
  });

  it("renders nothing when the model reported no bags", () => {
    const { container } = render(<DetectionOverlay detections={[]} />);

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("survives a processor that never sent detections", () => {
    const { container } = render(<DetectionOverlay detections={undefined} />);

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });
});

describe("DetectionOverlay — данные от старого ПК цеха", () => {
  // Сервис на ПК цеха обновляется вручную и может быть сильно старее CRM,
  // поэтому поля рамки нельзя считать гарантированными.

  it("survives a box that has no label at all", () => {
    // Реальный краш: label.split уронил всю страницу монитора.
    const broken = [{ x: 0.1, y: 0.1, w: 0.2, h: 0.2, counted: false }] as never;

    expect(() => render(<DetectionOverlay detections={broken} />)).not.toThrow();
  });

  it("drops a box with missing coordinates instead of drawing NaN", () => {
    const broken = [{ label: "Red_50", confidence: 0.9, counted: false }] as never;
    const { container } = render(<DetectionOverlay detections={broken} />);

    expect(container.innerHTML).not.toContain("NaN");
    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("keeps the good boxes when one row is broken", () => {
    const mixed = [{ label: "Red_50", confidence: 0.9, counted: false }, box({ label: "Blue_50" })] as never;
    render(<DetectionOverlay detections={mixed} />);

    // Одна битая запись не должна прятать остальные.
    expect(screen.getByText(/Blue_50/)).toBeInTheDocument();
    expect(screen.queryByText(/Red_50/)).not.toBeInTheDocument();
  });

  it("omits the percentage when confidence is missing", () => {
    const noConfidence = [{ x: 0.1, y: 0.1, w: 0.2, h: 0.2, label: "Red_50" }] as never;
    render(<DetectionOverlay detections={noConfidence} />);

    expect(screen.getByText(/Red_50/).textContent).not.toContain("%");
  });

  it("colours a labelless box with the fallback rather than crashing", () => {
    expect(bagColor(undefined)).toBe("#F79009");
    expect(bagColor(null)).toBe("#F79009");
  });
});

describe("DetectionOverlay — привязка к видео", () => {
  /**
   * Видео и оверлей — соседи в общем relative-боксе карточки, а не
   * вложенные друг в друга. Поиск видео внутри самого оверлея молча ничего
   * не находил: координаты приходили, счётчик на кнопке рос, а рамок на
   * экране не было.
   */
  function renderBesideVideo() {
    // jsdom не проигрывает видео и не раскладывает элементы, поэтому размеры
    // кадра и контейнера задаём вручную — иначе measure() нечего считать.
    Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", {
      configurable: true,
      value: 1920,
    });
    Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", {
      configurable: true,
      value: 1080,
    });
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      value: 800,
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      value: 600,
    });
    const result = render(
      <div style={{ position: "relative" }}>
        <video />
        <DetectionOverlay detections={[box()]} />
      </div>,
    );
    return result.container;
  }

  it("measures the video that sits next to it, not inside it", () => {
    const container = renderBesideVideo();
    const overlay = container.querySelector("[aria-hidden]") as HTMLElement;

    // Найдя видео, оверлей задаёт себе явную геометрию кадра. Пока видео
    // искали внутри себя, эффект выходил раньше и стили оставались пустыми —
    // рамки существовали, но были привязаны не к кадру.
    expect(overlay.style.left).toBe("0px");
    expect(overlay.style.top).toBe("75px");
    expect(overlay.style.width).toBe("800px");
    expect(overlay.style.height).toBe("450px");
    expect(overlay.querySelector("video")).toBeNull();
  });

  it("still renders when there is no video element at all", () => {
    expect(() => render(<DetectionOverlay detections={[box()]} />)).not.toThrow();
  });
});

describe("normalizeDetections — форматы AI-сервиса", () => {
  /**
   * ПК цеха обновляется вручную и живёт своей версией, поэтому в ответе
   * встречаются оба формата рамок. Именно из-за этого счётчик показывал
   * «Рамки модели · 1», а на экране не было ничего: пиксельный bbox не
   * попадал в поля x/y/w/h и запись отбрасывалась.
   */

  it("converts a pixel bbox into fractions of the frame", () => {
    const [drawn] = normalizeDetections(
      [{ bbox: [192, 108, 576, 540], class_name: "Red_50", confidence: 0.9 }] as never,
      { width: 1920, height: 1080 },
    );

    expect(drawn.x).toBeCloseTo(0.1);
    expect(drawn.y).toBeCloseTo(0.1);
    expect(drawn.w).toBeCloseTo(0.2);
    expect(drawn.h).toBeCloseTo(0.4);
    expect(drawn.label).toBe("Red_50");
    expect(drawn.color).toBe("#F04438");
  });

  it("keeps understanding the normalized format", () => {
    const [drawn] = normalizeDetections([box({ label: "Blue_50" })], { width: 1920, height: 1080 });

    expect(drawn.x).toBeCloseTo(0.1);
    expect(drawn.label).toBe("Blue_50");
  });

  it("drops a pixel bbox when the frame size is unknown", () => {
    // Без масштаба рамка легла бы не на тот мешок — лучше не рисовать.
    expect(normalizeDetections([{ bbox: [10, 10, 20, 20], class_name: "Red_50" }] as never, null)).toEqual([]);
    expect(normalizeDetections([{ bbox: [10, 10, 20, 20] }] as never, { width: 0, height: 0 })).toEqual([]);
  });

  it("reads class_name when label is absent", () => {
    const [drawn] = normalizeDetections([{ bbox: [0, 0, 96, 108], class_name: "Green_25" }] as never, {
      width: 960,
      height: 1080,
    });

    expect(drawn.label).toBe("Green_25");
    expect(drawn.color).toBe("#17B26A");
  });

  it("still drops a malformed bbox", () => {
    expect(
      normalizeDetections([{ bbox: ["a", null, 5, 5], class_name: "Red_50" }] as never, {
        width: 100,
        height: 100,
      }),
    ).toEqual([]);
  });

  it("clips a partly out-of-frame box to visible normalized bounds", () => {
    const [drawn] = normalizeDetections([{ x: -0.1, y: 0.8, w: 0.4, h: 0.4, label: "Red_50" }] as never);

    expect(drawn.x).toBe(0);
    expect(drawn.y).toBeCloseTo(0.8);
    expect(drawn.w).toBeCloseTo(0.3);
    expect(drawn.h).toBeCloseTo(0.2);
  });

  it("drops boxes with no visible positive-area intersection", () => {
    const invalid = [
      { x: 1.1, y: 0.1, w: 0.2, h: 0.2, label: "Right" },
      { x: 0.1, y: -0.4, w: 0.2, h: 0.2, label: "Above" },
      { x: 0.5, y: 0.5, w: -0.2, h: 0.2, label: "Inverted" },
      { x: 0.5, y: 0.5, w: 0.2, h: 0, label: "Flat" },
    ] as never;

    expect(normalizeDetections(invalid)).toEqual([]);
  });
});

describe("DetectionOverlay — устаревшие рамки", () => {
  /**
   * Мешок уезжает из кадра за секунды. Если связь оборвалась или модель
   * встала, последняя рамка иначе висит на пустом месте и врёт оператору.
   */

  it("hides boxes once their frame is older than the threshold", () => {
    const { container } = render(
      <DetectionOverlay detections={[box()]} updatedAt={Date.now() - 5_000} staleAfterMs={2_500} />,
    );

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("keeps a fresh box on screen", () => {
    render(<DetectionOverlay detections={[box()]} updatedAt={Date.now()} staleAfterMs={2_500} />);

    expect(screen.getByText(/Red_50/)).toBeInTheDocument();
  });

  it("removes a fresh box when the stale deadline passes without another response", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-08-24T10:00:00Z"));
      render(<DetectionOverlay detections={[box()]} updatedAt={Date.now()} staleAfterMs={2_500} />);
      expect(screen.getByText(/Red_50/)).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(2_499));
      expect(screen.getByText(/Red_50/)).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1));
      expect(screen.queryByText(/Red_50/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("never expires when no threshold is given", () => {
    // Старое поведение — рамка держится до следующего ответа.
    render(<DetectionOverlay detections={[box()]} updatedAt={Date.now() - 60_000} />);

    expect(screen.getByText(/Red_50/)).toBeInTheDocument();
  });
});
