import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GrainPage from "./page";

const postMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());
const reloadMock = vi.hoisted(() => vi.fn());
const pagedApiMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());
const visiblePollingMock = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));
vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Не удалось оформить вывоз",
}));
vi.mock("@/lib/can", () => ({
  can: () => true,
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => useApiMock(url),
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number, active?: boolean) =>
    visiblePollingMock(poll, intervalMs, active),
}));
vi.mock("@/lib/use-paged-api", () => ({
  usePagedApi: (url: string | null, pageSize: number) => pagedApiMock(url, pageSize),
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({
    me: { id: 1, username: "operator", permissions: ["grain.arrive", "grain.weigh"] },
  }),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, actions }: { children: ReactNode; actions?: ReactNode }) => (
    <main>
      {actions}
      {children}
    </main>
  ),
}));
vi.mock("@/components/grain/grain-toolbar", () => ({
  GrainToolbar: ({ direction, onPassage }: { direction: "intake" | "passage"; onPassage: () => void }) => (
    <div aria-label="Панель операций" data-direction={direction}>
      {direction === "passage" ? (
        <button type="button" onClick={onPassage}>
          Открыть вывоз
        </button>
      ) : (
        <span>Операции прихода</span>
      )}
    </div>
  ),
}));
vi.mock("@/components/grain/wagon-number-camera", () => ({
  WagonNumberCameraWorkspace: () => null,
}));
vi.mock("@/components/grain/wagon-table", () => ({
  FlowEmptyState: () => null,
  WagonTable: () => null,
}));
vi.mock("@/components/ui/modal", () => ({
  Modal: ({ open, title, children }: { open: boolean; title: string; children: ReactNode }) =>
    open ? <section aria-label={title}>{children}</section> : null,
}));

describe("Grain passage creation", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 91, number: "123 ABC" } });
    pushMock.mockReset();
    reloadMock.mockReset();
    pagedApiMock.mockReset();
    useApiMock.mockReset();
    useApiMock.mockReturnValue({ data: [], loading: false, error: "", reload: reloadMock });
    visiblePollingMock.mockReset();
    pagedApiMock.mockReturnValue({
      items: [],
      count: 0,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: reloadMock,
      loadMore: vi.fn(),
    });
  });

  it("loads separate intake and export tables with contextual tabs", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    expect(pagedApiMock).toHaveBeenCalledWith("/grain/wagons/?scope=on_site&direction=intake", 50);
    expect(screen.getByLabelText("Панель операций")).toHaveAttribute("data-direction", "intake");
    expect(screen.queryByRole("button", { name: "Открыть вывоз" })).not.toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Направление рейса" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Статус рейсов" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Ожидаются" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Камера проходной" })).toBeInTheDocument();
    expect(visiblePollingMock).toHaveBeenCalledWith(reloadMock, 10_000, true);

    await user.click(screen.getByRole("tab", { name: "Ожидаются" }));
    expect(screen.getByRole("tab", { name: "Ожидаются" })).toHaveAttribute("aria-selected", "true");
    expect(visiblePollingMock).toHaveBeenCalledWith(reloadMock, 10_000, false);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));

    await waitFor(() =>
      expect(pagedApiMock).toHaveBeenCalledWith("/grain/wagons/?scope=on_site&direction=passage", 50),
    );
    expect(screen.getByLabelText("Панель операций")).toHaveAttribute("data-direction", "passage");
    expect(screen.getByRole("button", { name: "Открыть вывоз" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Ожидаются" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Камера проходной" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "На территории" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Завершённые" })).toBeInTheDocument();
    expect(visiblePollingMock).toHaveBeenLastCalledWith(reloadMock, 10_000, true);
  });

  it("keeps the manual path when there are no camera candidates", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    expect(useApiMock).toHaveBeenCalledWith("/grain/wagons/vehicle-plate-candidates/");
    expect(visiblePollingMock).toHaveBeenCalledWith(expect.any(Function), 10_000, undefined);
    expect(screen.queryByRole("region", { name: "Распознанные номера" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Номер машины"), "123 ABC");
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(postMock).toHaveBeenCalledWith("/grain/wagons/passage/", {
      number: "123 ABC",
      cargo_name: "Отруби",
      note: "",
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/grain/wagons/91"));
  });

  it("uses only the explicitly selected camera candidate in the passage request", async () => {
    const candidate = {
      event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
      vehicle_number: "123ABC02",
      camera: "cam1",
      source: "main",
      detected_at: "2026-08-25T12:30:00.000Z",
      stationary_seconds: 3.4,
      ocr_confidence: 0.96,
    };
    useApiMock.mockReturnValue({ data: [candidate], loading: false, error: "", reload: reloadMock });
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));

    expect(screen.getByText("123ABC02")).toBeInTheDocument();
    expect(screen.getByText(/Камера cam1 · main/)).toBeInTheDocument();
    expect(screen.getByText(/OCR 96%/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Использовать" }));
    expect(screen.getByLabelText("Номер машины")).toHaveValue("123ABC02");

    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));
    expect(postMock).toHaveBeenCalledWith("/grain/wagons/passage/", {
      number: "123ABC02",
      cargo_name: "Отруби",
      note: "",
      vehicle_plate_event_id: candidate.event_id,
    });
  });

  it("switches to manual input explicitly before clearing the selected event", async () => {
    const candidate = {
      event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
      vehicle_number: "123ABC02",
      camera: "cam1",
      source: "main",
      detected_at: "2026-08-25T12:30:00.000Z",
      stationary_seconds: 3.4,
      ocr_confidence: 0.96,
    };
    useApiMock.mockReturnValue({ data: [candidate], loading: false, error: "", reload: reloadMock });
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    await user.click(screen.getByRole("button", { name: "Использовать" }));
    expect(screen.getByLabelText("Номер машины")).toHaveAttribute("readonly");
    await user.click(screen.getByRole("button", { name: "Перейти на ручной ввод" }));
    expect(screen.getByLabelText("Номер машины")).toHaveValue("");
    await user.type(screen.getByLabelText("Номер машины"), "999 XYZ 01");
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(postMock).toHaveBeenCalledWith("/grain/wagons/passage/", {
      number: "999 XYZ 01",
      cargo_name: "Отруби",
      note: "",
    });
  });

  it("keeps a selected candidate pinned when a poll removes it, then still submits its UUID", async () => {
    const candidate = {
      event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
      vehicle_number: "123ABC02",
      camera: "cam1",
      source: "main",
      detected_at: "2026-08-25T12:30:00.000Z",
      stationary_seconds: 3.4,
      ocr_confidence: 0.96,
    };
    useApiMock.mockReturnValue({ data: [candidate], loading: false, error: "", reload: reloadMock });
    const user = userEvent.setup();
    const { rerender } = render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    await user.click(screen.getByRole("button", { name: "Использовать" }));
    useApiMock.mockReturnValue({ data: [], loading: false, error: "", reload: reloadMock });
    rerender(<GrainPage />);

    expect(screen.getByText(/123ABC02 · выбран/)).toBeInTheDocument();
    expect(screen.getByText(/Камера cam1 · main/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));
    expect(postMock).toHaveBeenCalledWith("/grain/wagons/passage/", {
      number: "123ABC02",
      cargo_name: "Отруби",
      note: "",
      vehicle_plate_event_id: candidate.event_id,
    });
  });

  it("blocks an unavailable candidate until the operator explicitly switches to manual input", async () => {
    const candidate = {
      event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
      vehicle_number: "123ABC02",
      camera: "cam1",
      source: "main",
      detected_at: "2026-08-25T12:30:00.000Z",
      stationary_seconds: 3.4,
      ocr_confidence: 0.96,
    };
    useApiMock.mockReturnValue({ data: [candidate], loading: false, error: "", reload: reloadMock });
    postMock.mockRejectedValueOnce({ response: { data: { code: "vehicle_plate_event_unavailable" } } });
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    await user.click(screen.getByRole("button", { name: "Использовать" }));
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(await screen.findByText(/Выбранный номер больше недоступен/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Оформить вывоз/ })).toBeDisabled();
    expect(screen.getByText(/123ABC02 · недоступен/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Перейти на ручной ввод" }));
    expect(screen.getByRole("button", { name: /Оформить вывоз/ })).toBeEnabled();
    expect(screen.getByLabelText("Номер машины")).not.toHaveAttribute("readonly");
    expect(screen.getByLabelText("Номер машины")).toHaveValue("");
  });

  it("locks candidate controls during a deferred submission and keeps the error on that submitted candidate", async () => {
    const first = {
      event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
      vehicle_number: "123ABC02",
      camera: "cam1",
      source: "main",
      detected_at: "2026-08-25T12:30:00.000Z",
      stationary_seconds: 3.4,
      ocr_confidence: 0.96,
    };
    const second = { ...first, event_id: "5b2a3f76-a786-4f55-9af4-0fb3c38b16d2", vehicle_number: "456DEF02" };
    let rejectPost: (cause: unknown) => void = () => undefined;
    const delayedPost = new Promise<never>((_resolve, reject) => {
      rejectPost = reject;
    });
    useApiMock.mockReturnValue({ data: [first, second], loading: false, error: "", reload: reloadMock });
    postMock.mockReturnValueOnce(delayedPost);
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    await user.click(screen.getAllByRole("button", { name: "Использовать" })[0]);
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(screen.getByRole("button", { name: "Перейти на ручной ввод" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Использовать" })).toBeDisabled();
    expect(screen.getByLabelText("Номер машины")).toHaveValue("123ABC02");
    rejectPost({ response: { data: { code: "vehicle_plate_event_unavailable" } } });

    expect(await screen.findByText(/Выбранный номер больше недоступен/)).toBeInTheDocument();
    expect(screen.getByText(/123ABC02 · недоступен/)).toBeInTheDocument();
    expect(screen.queryByText(/456DEF02 · недоступен/)).not.toBeInTheDocument();
  });
});
