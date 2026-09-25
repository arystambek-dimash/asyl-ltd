import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VehicleRoiConfig } from "@/lib/types";
import { installVideoGeometry, restoreVideoGeometry } from "@/test-utils/video-geometry";
import { isValidRoiDraft, normalizeVehicleRoi, VehicleRoiOverlay } from "./vehicle-roi-overlay";

function roi(overrides: Partial<VehicleRoiConfig> = {}): VehicleRoiConfig {
  return {
    configured: true,
    enabled: true,
    source: "main",
    coordinate_space: "normalized",
    points: [
      { x: 0.1, y: 0.2 },
      { x: 0.6, y: 0.3 },
      { x: 0.95, y: 0.9 },
    ],
    ...overrides,
  };
}

describe("normalizeVehicleRoi", () => {
  it("accepts the canonical object points returned by the backend", () => {
    expect(
      normalizeVehicleRoi([
        { x: 0.1, y: 0.2 },
        { x: 0.6, y: 0.3 },
        { x: 0.95, y: 0.9 },
      ]),
    ).toEqual([
      [0.1, 0.2],
      [0.6, 0.3],
      [0.95, 0.9],
    ]);
  });

  it("rejects the whole polygon when a coordinate is malformed or outside the frame", () => {
    expect(
      normalizeVehicleRoi([
        { x: 0.1, y: 0.2 },
        { x: null, y: 0.3 },
        { x: 0.95, y: 0.9 },
      ]),
    ).toEqual([]);
    expect(
      normalizeVehicleRoi([
        [0.1, 0.2],
        [1.1, 0.3],
        [0.95, 0.9],
      ]),
    ).toEqual([]);
    expect(
      normalizeVehicleRoi([
        { x: 0.1, y: 0.2 },
        { x: 0.6, y: 0.3 },
      ]),
    ).toEqual([]);
  });

  it("rejects polygons larger than the camera-service contract", () => {
    expect(normalizeVehicleRoi(Array.from({ length: 13 }, (_, index) => ({ x: index / 13, y: 0.5 })))).toEqual([]);
  });
});

describe("isValidRoiDraft", () => {
  it("accepts 3..12 points with a real area", () => {
    expect(
      isValidRoiDraft([
        [0.1, 0.1],
        [0.9, 0.1],
        [0.5, 0.9],
      ]),
    ).toBe(true);
  });

  it("rejects too few or too many points and a degenerate polygon", () => {
    expect(
      isValidRoiDraft([
        [0.1, 0.1],
        [0.9, 0.9],
      ]),
    ).toBe(false);
    expect(isValidRoiDraft(Array.from({ length: 13 }, (_, index) => [index / 20, (index % 2) / 2] as const))).toBe(
      false,
    );
    expect(
      isValidRoiDraft([
        [0.1, 0.1],
        [0.5, 0.5],
        [0.9, 0.9],
      ]),
    ).toBe(false);
  });
});

describe("VehicleRoiOverlay", () => {
  afterEach(restoreVideoGeometry);

  it("aligns normalized points to the cropped object-cover video box", () => {
    installVideoGeometry();
    const { container } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "cover" }} />
        <VehicleRoiOverlay roi={roi()} expectedSource="main" />
      </div>,
    );

    const layer = screen.getByTestId("vehicle-roi-layer");
    expect(parseFloat(layer.style.left)).toBeCloseTo(-133.333, 2);
    expect(parseFloat(layer.style.top)).toBeCloseTo(0, 2);
    expect(parseFloat(layer.style.width)).toBeCloseTo(1066.667, 2);
    expect(parseFloat(layer.style.height)).toBeCloseTo(600, 2);
    expect(screen.getByTestId("vehicle-roi-polygon").querySelector("polygon")).toHaveAttribute(
      "points",
      "100,200 600,300 950,900",
    );
    expect(container).toHaveTextContent("ROI ОСТАНОВКИ");
  });

  it.each([
    ["disabled", roi({ enabled: false }), "main"],
    ["missing", roi({ configured: false }), "main"],
    ["source mismatch", roi({ source: "sub" }), "main"],
    ["wrong coordinate space", roi({ coordinate_space: "pixels" }), "main"],
    ["missing coordinate space", { ...roi(), coordinate_space: undefined } as unknown as VehicleRoiConfig, "main"],
    [
      "malformed",
      roi({
        points: [
          { x: 0.1, y: 0.2 },
          { x: null, y: 0.3 },
          { x: 0.8, y: 0.9 },
        ],
      }),
      "main",
    ],
  ])("does not draw a %s ROI", (_name, config, source) => {
    installVideoGeometry();
    render(
      <div>
        <video style={{ objectFit: "cover" }} />
        <VehicleRoiOverlay roi={config} expectedSource={source} />
      </div>,
    );

    expect(screen.queryByTestId("vehicle-roi-polygon")).not.toBeInTheDocument();
  });

  it("waits for video metadata instead of drawing against the card dimensions", () => {
    render(
      <div>
        <video style={{ objectFit: "cover" }} />
        <VehicleRoiOverlay roi={roi()} expectedSource="main" />
      </div>,
    );

    expect(screen.queryByTestId("vehicle-roi-polygon")).not.toBeInTheDocument();
  });

  it("exposes keyboard-accessible vertex controls only while editing", () => {
    installVideoGeometry();
    const onPointsChange = vi.fn();
    render(
      <div>
        <video style={{ objectFit: "cover" }} />
        <VehicleRoiOverlay roi={roi()} expectedSource="main" editable onPointsChange={onPointsChange} />
      </div>,
    );

    const firstPoint = screen.getByRole("button", { name: "Точка ROI 1" });
    fireEvent.keyDown(firstPoint, { key: "ArrowRight" });
    expect(onPointsChange).toHaveBeenLastCalledWith([
      [0.105, 0.2],
      [0.6, 0.3],
      [0.95, 0.9],
    ]);

    fireEvent.keyDown(firstPoint, { key: "ArrowUp", shiftKey: true });
    expect(onPointsChange).toHaveBeenLastCalledWith([
      [0.1, 0.18],
      [0.6, 0.3],
      [0.95, 0.9],
    ]);
  });

  it("converts pointer movement inside the object-cover video box back to normalized coordinates", () => {
    installVideoGeometry();
    const onPointsChange = vi.fn();
    render(
      <div>
        <video style={{ objectFit: "cover" }} />
        <VehicleRoiOverlay roi={roi()} expectedSource="main" editable onPointsChange={onPointsChange} />
      </div>,
    );

    const layer = screen.getByTestId("vehicle-roi-layer");
    vi.spyOn(layer, "getBoundingClientRect").mockReturnValue({
      x: 100,
      y: 50,
      left: 100,
      top: 50,
      right: 1100,
      bottom: 550,
      width: 1000,
      height: 500,
      toJSON: () => ({}),
    });
    const point = screen.getByRole("button", { name: "Точка ROI 2" });
    fireEvent.pointerDown(point, { pointerId: 4 });
    fireEvent.pointerMove(point, { pointerId: 4, clientX: 600, clientY: 425 });

    expect(onPointsChange).toHaveBeenLastCalledWith([
      [0.1, 0.2],
      [0.5, 0.75],
      [0.95, 0.9],
    ]);
  });
});
