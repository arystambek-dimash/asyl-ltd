import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DetectionOverlay, bagColor, normalizeDetections } from "./detection-overlay";
import type { AlwaysOnDetection } from "@/lib/types";
import { installVideoGeometry } from "@/test-utils/video-geometry";

// Кадр 1000×1000: пиксели рамки читаются как тысячные доли кадра.
const FRAME = { width: 1000, height: 1000 };

function box(overrides: Partial<AlwaysOnDetection> = {}): AlwaysOnDetection {
  return {
    bbox: [100, 200, 400, 600],
    class_name: "Red_50",
    confidence: 0.91,
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
  it("places a pixel box using fractions of the model frame", () => {
    render(<DetectionOverlay detections={[box()]} frame={FRAME} />);

    const drawn = screen.getByText(/Red_50/).parentElement!;
    expect(drawn.style.left).toBe("10%");
    expect(drawn.style.top).toBe("20%");
    expect(drawn.style.width).toBe("30%");
    expect(drawn.style.height).toBe("40%");
  });

  it("shows the confidence the model reported", () => {
    render(<DetectionOverlay detections={[box({ confidence: 0.91 })]} frame={FRAME} />);

    expect(screen.getByText(/91%/)).toBeInTheDocument();
  });

  it("keeps each box mounted when the processor changes detection order", () => {
    const { rerender } = render(
      <DetectionOverlay
        detections={[box({ class_name: "Red_50" }), box({ class_name: "Blue_50", bbox: [600, 200, 900, 600] })]}
        frame={FRAME}
      />,
    );
    const redBefore = screen.getByText(/Red_50/).parentElement;
    const blueBefore = screen.getByText(/Blue_50/).parentElement;

    // Model output is confidence-ordered, so two otherwise continuous tracks
    // can swap rows between polls. Remounting here defeats the CSS transition
    // and makes both boxes visibly jump instead of moving smoothly.
    rerender(
      <DetectionOverlay
        detections={[
          box({ class_name: "Blue_50", bbox: [620, 200, 920, 600] }),
          box({ class_name: "Red_50", bbox: [120, 200, 420, 600] }),
        ]}
        frame={FRAME}
      />,
    );

    expect(screen.getByText(/Red_50/).parentElement).toBe(redBefore);
    expect(screen.getByText(/Blue_50/).parentElement).toBe(blueBefore);
  });

  it("renders nothing when the model reported no bags", () => {
    const { container } = render(<DetectionOverlay detections={[]} frame={FRAME} />);

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("survives a processor that never sent detections", () => {
    const { container } = render(<DetectionOverlay detections={undefined} frame={FRAME} />);

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });
});

describe("DetectionOverlay — неполные данные с ПК цеха", () => {
  // Ответ ПК цеха приходит как есть, поэтому поля рамки нельзя считать
  // гарантированными.

  it("survives a box that has no class at all", () => {
    // Реальный краш: label.split уронил всю страницу монитора.
    const broken = [{ bbox: [100, 100, 300, 300] }] as never;

    expect(() => render(<DetectionOverlay detections={broken} frame={FRAME} />)).not.toThrow();
  });

  it("drops a box with missing coordinates instead of drawing NaN", () => {
    const broken = [{ class_name: "Red_50", confidence: 0.9 }] as never;
    const { container } = render(<DetectionOverlay detections={broken} frame={FRAME} />);

    expect(container.innerHTML).not.toContain("NaN");
    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("keeps the good boxes when one row is broken", () => {
    const mixed = [{ class_name: "Red_50", confidence: 0.9 }, box({ class_name: "Blue_50" })] as never;
    render(<DetectionOverlay detections={mixed} frame={FRAME} />);

    // Одна битая запись не должна прятать остальные.
    expect(screen.getByText(/Blue_50/)).toBeInTheDocument();
    expect(screen.queryByText(/Red_50/)).not.toBeInTheDocument();
  });

  it("omits the percentage when confidence is missing", () => {
    const noConfidence = [{ bbox: [100, 100, 300, 300], class_name: "Red_50" }] as never;
    render(<DetectionOverlay detections={noConfidence} frame={FRAME} />);

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
    installVideoGeometry();
    const result = render(
      <div style={{ position: "relative" }}>
        <video />
        <DetectionOverlay detections={[box()]} frame={FRAME} />
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
});

describe("normalizeDetections — пиксельные рамки AI-сервиса", () => {
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

  it("drops a pixel bbox when the frame size is unknown", () => {
    // Без масштаба рамка легла бы не на тот мешок — лучше не рисовать.
    expect(normalizeDetections([{ bbox: [10, 10, 20, 20], class_name: "Red_50" }] as never, null)).toEqual([]);
    expect(normalizeDetections([{ bbox: [10, 10, 20, 20] }] as never, { width: 0, height: 0 })).toEqual([]);
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
    const [drawn] = normalizeDetections([box({ bbox: [-100, 800, 300, 1200] })], FRAME);

    expect(drawn.x).toBe(0);
    expect(drawn.y).toBeCloseTo(0.8);
    expect(drawn.w).toBeCloseTo(0.3);
    expect(drawn.h).toBeCloseTo(0.2);
  });

  it("drops boxes with no visible positive-area intersection", () => {
    const invalid = [
      box({ class_name: "Right", bbox: [1100, 100, 1300, 300] }),
      box({ class_name: "Above", bbox: [100, -400, 300, -200] }),
      box({ class_name: "Inverted", bbox: [500, 500, 300, 700] }),
      box({ class_name: "Flat", bbox: [500, 500, 700, 500] }),
    ];

    expect(normalizeDetections(invalid, FRAME)).toEqual([]);
  });
});

describe("DetectionOverlay — устаревшие рамки", () => {
  /**
   * Мешок уезжает из кадра за секунды. Если связь оборвалась или модель
   * встала, последняя рамка иначе висит на пустом месте и врёт оператору.
   */

  it("hides boxes once their frame is older than the threshold", () => {
    const { container } = render(
      <DetectionOverlay detections={[box()]} frame={FRAME} updatedAt={Date.now() - 5_000} />,
    );

    expect(container.querySelectorAll("span")).toHaveLength(0);
  });

  it("keeps a fresh box on screen", () => {
    render(<DetectionOverlay detections={[box()]} frame={FRAME} updatedAt={Date.now()} />);

    expect(screen.getByText(/Red_50/)).toBeInTheDocument();
  });

  it("removes a fresh box when the stale deadline passes without another response", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-08-24T10:00:00Z"));
      render(<DetectionOverlay detections={[box()]} frame={FRAME} updatedAt={Date.now()} />);
      expect(screen.getByText(/Red_50/)).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(2_499));
      expect(screen.getByText(/Red_50/)).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(1));
      expect(screen.queryByText(/Red_50/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
