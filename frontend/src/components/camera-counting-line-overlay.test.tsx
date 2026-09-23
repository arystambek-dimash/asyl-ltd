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

const BEFORE = { id: "before", name: "До механизма", line: { x1: 0.25, y1: 0.2, x2: 0.25, y2: 0.8 } };

function renderEditor(props: Partial<React.ComponentProps<typeof CameraCountingLineOverlay>> = {}) {
  installVideoGeometry();
  const handlers = {
    onLineChange: vi.fn(),
    onVerificationLineChange: vi.fn(),
    onActiveLineChange: vi.fn(),
  };
  const view = render(
    <div style={{ position: "relative" }}>
      <video style={{ objectFit: "contain" }} />
      <CameraCountingLineOverlay
        line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }}
        direction="any"
        verificationLines={[BEFORE]}
        editable
        {...handlers}
        {...props}
      />
    </div>,
  );
  const overlay = view.container.querySelector("[data-camera-counting-line]") as HTMLElement;
  overlay.getBoundingClientRect = () =>
    ({ left: 0, top: 75, width: 800, height: 450, right: 800, bottom: 525, x: 0, y: 75, toJSON() {} }) as DOMRect;
  overlay.setPointerCapture = vi.fn();
  overlay.hasPointerCapture = vi.fn(() => false);
  return { ...view, overlay, ...handlers };
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
    fireEvent.pointerMove(overlay, { clientX: 600, clientY: 390, pointerId: 1 });

    expect(onLineChange).toHaveBeenLastCalledWith({ x1: 0.5, y1: 0.5, x2: 0.75, y2: 0.7 });
  });

  it("shows saved verification lines read-only next to the counting line", () => {
    installVideoGeometry();
    const { container } = render(
      <div style={{ position: "relative" }}>
        <video style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay
          line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }}
          direction="any"
          verificationLines={[BEFORE]}
        />
      </div>,
    );

    const layer = container.querySelector('[data-verification-line="before"]')!;
    const stroke = layer.querySelector("[data-verification-stroke]")!;
    expect(stroke.getAttribute("x1")).toBe("250");
    expect(stroke.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(layer).toHaveTextContent("До механизма");
    expect(container.querySelector("[data-camera-counting-line]")).toHaveClass("pointer-events-none");
  });

  it("drags a verification endpoint and selects that line without touching the count line", () => {
    const { overlay, onLineChange, onVerificationLineChange, onActiveLineChange } = renderEditor();

    fireEvent.pointerDown(overlay, { clientX: 200, clientY: 165, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 400, clientY: 300, pointerId: 1 });

    expect(onActiveLineChange).toHaveBeenCalledWith("before");
    expect(onVerificationLineChange).toHaveBeenCalledWith("before", { x1: 0.5, y1: 0.5, x2: 0.25, y2: 0.8 });
    expect(onLineChange).not.toHaveBeenCalled();
  });

  it("selects a verification line by its body without redrawing anything", () => {
    const { overlay, onLineChange, onVerificationLineChange, onActiveLineChange } = renderEditor();

    fireEvent.pointerDown(overlay, { clientX: 202, clientY: 300, pointerId: 1 });

    expect(onActiveLineChange).toHaveBeenCalledWith("before");
    expect(onLineChange).not.toHaveBeenCalled();
    expect(onVerificationLineChange).not.toHaveBeenCalled();
  });

  it("redraws the active verification line from an empty spot", () => {
    const { overlay, onLineChange, onVerificationLineChange } = renderEditor({ activeLineId: "before" });

    fireEvent.pointerDown(overlay, { clientX: 600, clientY: 120, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 600, clientY: 480, pointerId: 1 });

    expect(onVerificationLineChange).toHaveBeenLastCalledWith("before", { x1: 0.75, y1: 0.1, x2: 0.75, y2: 0.9 });
    expect(onLineChange).not.toHaveBeenCalled();
  });

  it("never changes geometry on a plain tap or a finger's jitter", () => {
    const { overlay, onLineChange, onVerificationLineChange } = renderEditor();

    fireEvent.pointerDown(overlay, { clientX: 600, clientY: 120, pointerId: 1, pointerType: "touch" });
    fireEvent.pointerMove(overlay, { clientX: 603, clientY: 122, pointerId: 1, pointerType: "touch" });
    fireEvent.pointerUp(overlay, { clientX: 603, clientY: 122, pointerId: 1, pointerType: "touch" });

    expect(onLineChange).not.toHaveBeenCalled();
    expect(onVerificationLineChange).not.toHaveBeenCalled();
  });

  it("gives a finger a wider reach when selecting a line by its body", () => {
    const { overlay, onLineChange, onActiveLineChange } = renderEditor();

    // 20 px beside the vertical «До механизма» line at x = 200.
    fireEvent.pointerDown(overlay, { clientX: 220, clientY: 300, pointerId: 1, pointerType: "touch" });
    expect(onActiveLineChange).toHaveBeenCalledWith("before");

    onActiveLineChange.mockClear();
    fireEvent.pointerDown(overlay, { clientX: 220, clientY: 300, pointerId: 2, pointerType: "mouse" });
    expect(onActiveLineChange).not.toHaveBeenCalled();
    expect(onLineChange).not.toHaveBeenCalled();
  });

  it("binds to a still frame when live video is unavailable", () => {
    installVideoGeometry();
    Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", { configurable: true, value: 1920 });
    Object.defineProperty(HTMLImageElement.prototype, "naturalHeight", { configurable: true, value: 1080 });
    const { container } = render(
      <div style={{ position: "relative" }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- mirrors the editor's blob: still frame */}
        <img data-video-box-source alt="" style={{ objectFit: "contain" }} />
        <CameraCountingLineOverlay line={{ x1: 0.1, y1: 0.2, x2: 0.8, y2: 0.9 }} direction="any" />
      </div>,
    );

    const overlay = container.querySelector("[data-camera-counting-line]") as HTMLElement;
    expect(overlay).toHaveAttribute("data-video-box-ready", "true");
    expect(overlay.style.top).toBe("75px");
    expect(overlay.style.height).toBe("450px");
  });
});
