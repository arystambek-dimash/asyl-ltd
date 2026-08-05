import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DetectionOverlay, bagColor } from "./detection-overlay";
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
