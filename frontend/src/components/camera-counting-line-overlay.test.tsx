import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CameraCountingLineOverlay } from "./camera-counting-line-overlay";

function installVideoGeometry() {
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
}

describe("CameraCountingLineOverlay", () => {
  it("does not expose editable coordinates before video metadata is known", () => {
    Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", {
      configurable: true,
      value: 0,
    });
    Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", {
      configurable: true,
      value: 0,
    });
    const onLineChange = vi.fn();
    const { container } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay
          line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }}
          direction="any"
          editable
          onLineChange={onLineChange}
        />
      </div>,
    );
    const overlay = container.querySelector("[data-camera-counting-line]") as HTMLElement;

    expect(overlay).toHaveAttribute("data-video-box-ready", "false");
    fireEvent.pointerDown(overlay, { clientX: 400, clientY: 300, pointerId: 1 });
    expect(onLineChange).not.toHaveBeenCalled();
  });

  it("binds normalized coordinates to the contained video, not its letterbox", () => {
    installVideoGeometry();
    const { container } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }} direction="negative" />
      </div>,
    );

    const overlay = container.querySelector("[data-camera-counting-line]") as HTMLElement;
    expect(overlay.style.left).toBe("0px");
    expect(overlay.style.top).toBe("75px");
    expect(overlay.style.width).toBe("800px");
    expect(overlay.style.height).toBe("450px");

    const line = container.querySelector('[data-counting-line="primary"]')!;
    expect(line.getAttribute("x1")).toBe("100");
    expect(line.getAttribute("x2")).toBe("800");
    expect(container.querySelector("[data-counting-direction=negative]")).toBeInTheDocument();
  });

  it("updates the existing layer when a remote processor reports a new line", () => {
    installVideoGeometry();
    const { container, rerender } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }} direction="up" />
      </div>,
    );
    const overlay = container.querySelector("[data-camera-counting-line]");

    rerender(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay line={{ x1: 0.25, y1: 0.4, x2: 0.75, y2: 0.6 }} direction="down" />
      </div>,
    );

    expect(container.querySelector("[data-camera-counting-line]")).toBe(overlay);
    expect(container.querySelector('[data-counting-line="primary"]')?.getAttribute("x1")).toBe("250");
    expect(container.querySelector("[data-counting-direction=down]")).toBeInTheDocument();
  });

  it("normalizes editor pointer coordinates against the visible video box", () => {
    installVideoGeometry();
    const onLineChange = vi.fn();
    const { container } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay
          line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }}
          direction="any"
          editable
          onLineChange={onLineChange}
        />
      </div>,
    );
    const overlay = container.querySelector("[data-camera-counting-line]") as HTMLElement;
    overlay.getBoundingClientRect = () =>
      ({ left: 0, top: 75, width: 800, height: 450, right: 800, bottom: 525, x: 0, y: 75, toJSON() {} }) as DOMRect;
    overlay.setPointerCapture = vi.fn();

    fireEvent.pointerDown(overlay, { clientX: 400, clientY: 300, pointerId: 1 });

    expect(onLineChange).toHaveBeenCalledWith({ x1: 0.5, y1: 0.5, x2: 0.5, y2: 0.5 });
  });
});
