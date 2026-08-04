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
