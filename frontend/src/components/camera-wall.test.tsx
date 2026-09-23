import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { CameraTile, CameraWall, type CameraFeed } from "./camera-wall";

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), patch: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, put: mocks.put, patch: mocks.patch },
}));

vi.mock("@/components/camera-stream", () => ({
  CameraStream: () => null,
  ensureCameraStreamToken: vi.fn(),
}));

const camera: CameraFeed & { src: string } = {
  id: "direct-aa:bb",
  name: "cam1",
  zone: "Главные ворота",
  src: "cam1",
  kind: "direct",
  online: true,
};

describe("CameraTile", () => {
  it("opens through a native keyboard-accessible action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();

    render(<CameraTile cam={camera} ready={false} onOnline={vi.fn()} onClick={onClick} />);

    const open = screen.getByRole("button", { name: "Открыть камеру «Главные ворота»" });
    await user.tab();
    expect(open).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("keeps camera controls separate from the tile action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const onRename = vi.fn();
    const onConfigureLine = vi.fn();

    render(
      <CameraTile
        cam={camera}
        ready={false}
        onOnline={vi.fn()}
        onClick={onClick}
        onRename={onRename}
        onConfigureLine={onConfigureLine}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Изменить название камеры" }));
    await user.click(screen.getByRole("button", { name: "Настроить линию подсчёта" }));

    expect(onRename).toHaveBeenCalledWith(camera);
    expect(onConfigureLine).toHaveBeenCalledWith(camera);
    expect(onClick).not.toHaveBeenCalled();
  });
});

const COUNT = { x1: 0.1, y1: 0.5, x2: 0.9, y2: 0.5 };
const BEFORE = { id: "before", name: "До механизма", line: { x1: 0.714, y1: 0.354, x2: 0.744, y2: 0.716 } };
const LINE_CONFIG = {
  configured: true,
  coordinate_space: "normalized",
  line: COUNT,
  line_spec: "0.1,0.5,0.9,0.5",
  direction: "any",
  updated_at: "2026-09-23T10:00:00+00:00",
  verification_lines: [BEFORE],
  verification_lines_supported: true,
};
const WALL_CAMERA = {
  id: "nvr:cam3",
  name: "cam3",
  zone: "Конвейер вагон",
  src: "cam3",
  kind: "nvr-channel",
  online: true,
};

function serve(config: Record<string, unknown> = LINE_CONFIG, lineApplied?: string) {
  mocks.get.mockImplementation(async (url: string, options?: { params?: Record<string, unknown> }) => {
    if (url === "/cameras/") return { data: [{ ...WALL_CAMERA, line_config: config }] };
    if (url === "/cameras/cam3/counting-line") {
      return { data: options?.params?.applied ? { ...config, line_applied: lineApplied } : config };
    }
    return new Promise(() => undefined);
  });
}

async function openLineEditor() {
  render(<CameraWall />);
  fireEvent.click(await screen.findByRole("button", { name: "Настроить линию подсчёта" }));
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByDisplayValue("До механизма");
  return dialog;
}

describe("CameraWall line editor", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.put.mockReset();
    useAuth.setState({ me: { is_superuser: true, permissions: [] } as unknown as Me });
  });

  afterEach(() => {
    useAuth.setState({ me: null });
  });

  it("saves the counting line with verification lines and applies the server reply", async () => {
    serve();
    mocks.put.mockResolvedValue({
      data: {
        ok: true,
        saved: true,
        applied_to_processor: true,
        ...LINE_CONFIG,
        updated_at: "2026-09-23T10:05:00+00:00",
        verification_lines: [{ ...BEFORE, name: "Перед механизмом", line_spec: "0.714,0.354,0.744,0.716" }],
      },
    });
    const dialog = await openLineEditor();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "Название линии проверки 1" }), {
      target: { value: "  Перед механизмом " },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить линии" }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put).toHaveBeenCalledWith(
      "/cameras/cam3/counting-line",
      { line: COUNT, direction: "any", verification_lines: [{ ...BEFORE, name: "Перед механизмом" }] },
      { timeout: 12_000 },
    );
    expect(await within(dialog).findByText("Линии сохранены и готовы к подсчёту.")).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Название линии проверки 1" })).toHaveValue("Перед механизмом");
  });

  it("blocks saving with the reason shown inside the modal", async () => {
    serve();
    const dialog = await openLineEditor();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "Название линии проверки 1" }), {
      target: { value: "   " },
    });

    expect(within(dialog).getByText("Укажите название для каждой линии проверки.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Сохранить линии" })).toBeDisabled();
  });

  it.each([
    ["not_applied", "Камера всё ещё работает со старыми линиями. Сохраните ещё раз.", null],
    ["applied", null, "Линии применены к камере."],
    ["not_running", null, "Модель на камере не запущена — линии применятся при следующем запуске."],
  ])("refreshes a saved-but-not-applied reply against the running camera: %s", async (state, warning, notice) => {
    serve(LINE_CONFIG, state);
    mocks.put.mockRejectedValue({
      response: {
        status: 503,
        data: {
          saved: true,
          applied_to_processor: false,
          code: "saved_not_applied",
          detail: "Сохранено, но не применено к камере — обновите статус",
          ...LINE_CONFIG,
        },
      },
    });
    const dialog = await openLineEditor();

    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить линии" }));

    expect(
      await within(dialog).findByText("Сохранено, но не применено к камере — обновите статус"),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Обновить статус" }));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/cameras/cam3/counting-line", {
        timeout: 10_000,
        params: { applied: 1 },
      }),
    );

    if (warning) {
      expect(await within(dialog).findByText(warning)).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Обновить статус" })).toBeInTheDocument();
    } else {
      expect(await within(dialog).findByText(notice!)).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "Обновить статус" })).not.toBeInTheDocument();
    }
  });

  it("follows an AI-service upgrade while the editor is open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const legacy = { ...LINE_CONFIG, verification_lines: [], verification_lines_supported: false };
      serve(legacy);
      render(<CameraWall />);
      fireEvent.click(await screen.findByRole("button", { name: "Настроить линию подсчёта" }));
      const dialog = await screen.findByRole("dialog");
      expect(await within(dialog).findByText(/Обновите AI-сервис/)).toBeInTheDocument();

      // Same line, same timestamp, still no lines — only the support changed.
      serve({ ...legacy, verification_lines_supported: true });
      await act(async () => {
        vi.advanceTimersByTime(3_000);
      });

      await waitFor(() => expect(within(dialog).queryByText(/Обновите AI-сервис/)).not.toBeInTheDocument());
      expect(within(dialog).getByRole("button", { name: "Добавить линию проверки" })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaves AI-side lines untouched when the AI service cannot store them", async () => {
    serve({ ...LINE_CONFIG, verification_lines: [], verification_lines_supported: false });
    mocks.put.mockResolvedValue({ data: { ok: true, saved: true, applied_to_processor: true, ...LINE_CONFIG } });
    render(<CameraWall />);
    fireEvent.click(await screen.findByRole("button", { name: "Настроить линию подсчёта" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Обновите AI-сервис/)).toBeInTheDocument();

    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Сохранить линии" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить линии" }));

    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put.mock.calls[0][1]).toEqual({ line: COUNT, direction: "any" });
  });
});
