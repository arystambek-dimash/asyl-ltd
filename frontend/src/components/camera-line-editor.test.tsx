import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NormalizedLine, VerificationLine } from "@/lib/camera-counting-line";
import { CameraLineEditor, LIVE_FALLBACK_MS } from "./camera-line-editor";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  /** The mounted stream's onStateChange: lets a test connect the live video. */
  reportOnline: (() => undefined) as (online: boolean) => void,
}));

vi.mock("@/lib/api", () => ({
  api: { get: mocks.get },
  blobApiError: async (cause: { message?: string }) => cause.message ?? "Ошибка",
}));

vi.mock("@/components/camera-stream", () => ({
  // The live stream stays «Подключение…» unless a test reports it online.
  CameraStream: ({ onStateChange }: { onStateChange?: (online: boolean) => void }) => {
    mocks.reportOnline = onStateChange ?? (() => undefined);
    return <video data-testid="live-video" style={{ objectFit: "contain" }} />;
  },
}));

const COUNT: NormalizedLine = { x1: 0.1, y1: 0.5, x2: 0.9, y2: 0.5 };
const BEFORE: VerificationLine = {
  id: "before",
  name: "До механизма",
  line: { x1: 0.25, y1: 0.2, x2: 0.25, y2: 0.8 },
};

function Harness({
  initial = [],
  supported = true,
  onLines = () => undefined,
}: {
  initial?: VerificationLine[];
  supported?: boolean;
  onLines?: (lines: VerificationLine[]) => void;
}) {
  const [line, setLine] = useState(COUNT);
  const [lines, setLines] = useState(initial);
  return (
    <CameraLineEditor
      src="cam3"
      line={line}
      direction="any"
      ready
      verificationLines={lines}
      verificationSupported={supported}
      onLineChange={setLine}
      onDirectionChange={() => undefined}
      onVerificationLinesChange={(next) => {
        setLines(next);
        onLines(next);
      }}
    />
  );
}

function installGeometry() {
  Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", { configurable: true, value: 1920 });
  Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", { configurable: true, value: 1080 });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, value: 800 });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
}

function overlaySurface(container: HTMLElement) {
  const overlay = container.querySelector("[data-camera-counting-line]") as HTMLElement;
  overlay.getBoundingClientRect = () =>
    ({ left: 0, top: 75, width: 800, height: 450, right: 800, bottom: 525, x: 0, y: 75, toJSON() {} }) as DOMRect;
  overlay.setPointerCapture = vi.fn();
  overlay.hasPointerCapture = vi.fn(() => false);
  return overlay;
}

describe("CameraLineEditor verification lines", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockReturnValue(new Promise(() => undefined));
  });

  it("explains that checks classify while only the main line counts", () => {
    render(<Harness />);
    expect(
      screen.getByText("Мешок классифицируется на каждой линии проверки; счёт — только по основной линии."),
    ).toBeInTheDocument();
  });

  it("adds a line with a stable slug id, default name and its own colour", () => {
    const onLines = vi.fn();
    render(<Harness onLines={onLines} />);

    fireEvent.click(screen.getByRole("button", { name: "Добавить линию проверки" }));

    const [created] = onLines.mock.lastCall![0] as VerificationLine[];
    expect(created).toMatchObject({ id: "check-1", name: "Проверка 1" });
    expect(screen.getByRole("textbox", { name: "Название линии проверки 1" })).toHaveValue("Проверка 1");
    expect(screen.getByText("1 из 8")).toBeInTheDocument();
  });

  it("renames without changing the id and deletes a line", () => {
    const onLines = vi.fn();
    render(<Harness initial={[BEFORE]} onLines={onLines} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Название линии проверки 1" }), {
      target: { value: "Перед механизмом" },
    });
    expect(onLines.mock.lastCall![0]).toEqual([{ ...BEFORE, name: "Перед механизмом" }]);

    fireEvent.click(screen.getByRole("button", { name: "Удалить линию «Перед механизмом»" }));
    expect(onLines.mock.lastCall![0]).toEqual([]);
  });

  it("draws the selected verification line on the image, not the counting line", () => {
    installGeometry();
    const onLines = vi.fn();
    const { container } = render(<Harness initial={[BEFORE]} onLines={onLines} />);

    fireEvent.click(screen.getByRole("button", { name: "Выбрать линию «До механизма»" }));
    const overlay = overlaySurface(container);
    fireEvent.pointerDown(overlay, { clientX: 600, clientY: 120, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 600, clientY: 480, pointerId: 1 });

    expect(onLines.mock.lastCall![0]).toEqual([{ ...BEFORE, line: { x1: 0.75, y1: 0.1, x2: 0.75, y2: 0.9 } }]);
  });

  it("disables the section with an upgrade hint on an older AI service", () => {
    render(<Harness supported={false} />);

    expect(screen.getByText(/Обновите AI-сервис/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Добавить линию проверки" })).toBeDisabled();
  });
});

describe("CameraLineEditor still frame", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.get.mockReset();
    let objectUrls = 0;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => `blob:frame-${++objectUrls}`),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  it("shows the camera's still frame when live video does not connect, and refreshes it", async () => {
    mocks.get.mockResolvedValue({ data: new Blob(["jpeg"], { type: "image/jpeg" }) });
    render(<Harness />);
    expect(screen.getByText("Подключение…")).toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(LIVE_FALLBACK_MS);
    });
    await flush();

    expect(mocks.get).toHaveBeenCalledWith("/cameras/cam3/counting-line/frame", {
      responseType: "blob",
      timeout: 15_000,
    });
    expect(screen.getByAltText("Кадр камеры для разметки")).toHaveAttribute("src", "blob:frame-1");
    expect(screen.queryByTestId("live-video")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Обновить кадр" }));
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.getByAltText("Кадр камеры для разметки")).toHaveAttribute("src", "blob:frame-2");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:frame-1");

    fireEvent.click(screen.getByRole("button", { name: "Живое видео" }));
    expect(screen.getByTestId("live-video")).toBeInTheDocument();
  });

  it("keeps the live stream when the automatic frame request fails, and does not retry on its own", async () => {
    mocks.get.mockRejectedValue(new Error("Нет свежего кадра: камера не подключена к AI."));
    render(<Harness />);

    await act(async () => {
      vi.advanceTimersByTime(LIVE_FALLBACK_MS);
    });
    await flush();
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("live-video")).toBeInTheDocument();
    expect(screen.getByText("Подключение…")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Нет свежего кадра: камера не подключена к AI.");

    await act(async () => {
      vi.advanceTimersByTime(LIVE_FALLBACK_MS * 3);
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("does not replace a stream that connected while the frame was loading", async () => {
    let deliver: (value: { data: Blob }) => void = () => undefined;
    mocks.get.mockReturnValue(new Promise((resolve) => (deliver = resolve)));
    render(<Harness />);

    await act(async () => {
      vi.advanceTimersByTime(LIVE_FALLBACK_MS);
    });
    act(() => mocks.reportOnline(true));
    await act(async () => deliver({ data: new Blob(["jpeg"], { type: "image/jpeg" }) }));
    await flush();

    expect(screen.getByTestId("live-video")).toBeInTheDocument();
    expect(screen.getByText("Живое видео")).toBeInTheDocument();
  });

  it("starts a returning live stream as connecting and leaves the choice to the operator", async () => {
    mocks.get.mockResolvedValue({ data: new Blob(["jpeg"], { type: "image/jpeg" }) });
    render(<Harness />);
    act(() => mocks.reportOnline(true));
    expect(screen.getByText("Живое видео")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Показать кадр" }));
    await flush();
    expect(screen.getByText("Снимок кадра")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Живое видео" }));
    expect(screen.getByText("Подключение…")).toBeInTheDocument();
    expect(screen.getByTestId("live-video")).toBeInTheDocument();

    // A slow stream gets its full startup time once the operator chose it.
    await act(async () => {
      vi.advanceTimersByTime(LIVE_FALLBACK_MS * 2);
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("live-video")).toBeInTheDocument();
  });

  it("switches to the still frame on demand and shows its error inside the editor", async () => {
    mocks.get.mockRejectedValue(new Error("Нет свежего кадра: камера не подключена к AI."));
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Показать кадр" }));
    await flush();
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Нет свежего кадра: камера не подключена к AI.");
  });
});
