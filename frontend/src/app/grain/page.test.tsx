import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GrainPage from "./page";
import PassagePage from "./passages/page";

const TODAY = "2026-09-06";
const postMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());
const reloadMock = vi.hoisted(() => vi.fn());
const pagedApiMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());
const visiblePollingMock = vi.hoisted(() => vi.fn());
const localDayMock = vi.hoisted(() => vi.fn<() => string>());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));
vi.mock("@/lib/use-local-day", () => ({ useLocalDay: () => localDayMock() }));
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
vi.mock("@/lib/use-debounced", () => ({ useDebounced: (value: string) => value }));
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
  AppShell: ({
    children,
    actions,
    description,
  }: {
    children: ReactNode;
    actions?: ReactNode;
    description?: string;
  }) => (
    <main>
      {actions}
      {description && <p>{description}</p>}
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
  WagonNumberCameraWorkspace: () => <section aria-label="Камера вагонов на приход" />,
}));
vi.mock("@/components/grain/vehicle-plate-camera", () => ({
  VehiclePlateCameraWorkspace: () => <section aria-label="Камера машин на вывоз" />,
}));
vi.mock("@/components/grain/wagon-table", () => ({
  FlowEmptyState: () => null,
  WagonTable: ({ emptyText }: { emptyText: string }) => <p data-testid="wagon-table">{emptyText}</p>,
}));
vi.mock("@/components/ui/modal", () => ({
  Modal: ({
    open,
    title,
    description,
    children,
  }: {
    open: boolean;
    title: string;
    description?: string;
    children: ReactNode;
  }) =>
    open ? (
      <section aria-label={title}>
        {description && <p>{description}</p>}
        {children}
      </section>
    ) : null,
}));

describe("Grain passage creation", () => {
  beforeEach(() => {
    localDayMock.mockReturnValue(TODAY);
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

  it("opens outbound trips directly from their own route", () => {
    render(<PassagePage />);
    expect(screen.getByLabelText("Панель операций")).toHaveAttribute("data-direction", "passage");
    expect(pagedApiMock).toHaveBeenCalledWith("/grain/passages/?scope=on_site&direction=passage", 50);
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
      expect(pagedApiMock).toHaveBeenCalledWith("/grain/passages/?scope=on_site&direction=passage", 50),
    );
    expect(screen.getByLabelText("Панель операций")).toHaveAttribute("data-direction", "passage");
    expect(screen.getByRole("button", { name: "Открыть вывоз" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Ожидаются" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Камера проходной" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "На территории" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Завершённые" })).toBeInTheDocument();
    expect(visiblePollingMock).toHaveBeenCalledWith(reloadMock, 10_000, true);
    expect(screen.getByText(/Доступность автоматики показана во вкладке «Камера проходной»/)).toBeInTheDocument();
  });

  it("keeps the intake and export camera tabs isolated", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Камера проходной" }));
    expect(screen.getByRole("region", { name: "Камера вагонов на приход" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    expect(screen.getByRole("tab", { name: "На территории" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("region", { name: "Камера вагонов на приход" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Камера машин на вывоз" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Камера проходной" }));
    expect(screen.getByRole("region", { name: "Камера машин на вывоз" })).toBeInTheDocument();
    expect(visiblePollingMock).toHaveBeenLastCalledWith(reloadMock, 10_000, false);

    await user.click(screen.getByRole("tab", { name: "Приход" }));
    expect(screen.getByRole("tab", { name: "Камера проходной" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Камера вагонов на приход" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    expect(screen.getByRole("tab", { name: "Камера проходной" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Камера машин на вывоз" })).toBeInTheDocument();
  });

  it("keeps the manual path when there are no camera candidates", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("button", { name: "Открыть вывоз" }));
    expect(screen.getByText(/Используйте ручное оформление, если автоматика/)).toBeInTheDocument();
    expect(useApiMock).toHaveBeenCalledWith("/grain/passages/vehicle-plate-candidates/");
    expect(visiblePollingMock).toHaveBeenCalledWith(expect.any(Function), 10_000, undefined);
    expect(screen.queryByRole("region", { name: "Распознанные номера" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Номер машины"), "123 ABC");
    await user.click(screen.getByRole("button", { name: /Оформить вывоз/ }));

    expect(postMock).toHaveBeenCalledWith("/grain/passages/", {
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
    expect(postMock).toHaveBeenCalledWith("/grain/passages/", {
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

    expect(postMock).toHaveBeenCalledWith("/grain/passages/", {
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
    expect(postMock).toHaveBeenCalledWith("/grain/passages/", {
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

describe("Grain list filters", () => {
  /** Последний адрес списка рейсов: после него хук ещё вызывается для поставок. */
  function lastWagonsUrl() {
    const urls = pagedApiMock.mock.calls
      .map(([url]) => url as string | null)
      .filter((url) => url?.startsWith("/grain/wagons/") || url?.startsWith("/grain/passages/"));
    return urls[urls.length - 1];
  }

  const emptyList = {
    items: [],
    count: 0,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
    reload: reloadMock,
    loadMore: vi.fn(),
  };

  beforeEach(() => {
    localDayMock.mockReturnValue(TODAY);
    reloadMock.mockReset();
    pagedApiMock.mockReset();
    useApiMock.mockReset();
    useApiMock.mockReturnValue({ data: [], loading: false, error: "", reload: reloadMock });
    visiblePollingMock.mockReset();
    pagedApiMock.mockReturnValue(emptyList);
  });

  it("shows finished trips for today by default and «Все дни» drops the day filter and polling", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    expect(screen.queryByLabelText("День")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Завершённые" }));

    expect(screen.getByLabelText("День")).toHaveValue(TODAY);
    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        `/grain/wagons/?scope=finished&direction=intake&date_from=${TODAY}&date_to=${TODAY}`,
      ),
    );
    expect(visiblePollingMock).toHaveBeenLastCalledWith(reloadMock, 10_000, true);

    await user.click(screen.getByRole("button", { name: "Все дни" }));
    expect(screen.getByLabelText("День")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Все дни" })).toBeDisabled();
    await waitFor(() => expect(lastWagonsUrl()).toBe("/grain/wagons/?scope=finished&direction=intake"));
    // Архив не опрашивается: иначе подгруженные «Показать ещё» страницы схлопывались бы.
    expect(visiblePollingMock).toHaveBeenLastCalledWith(reloadMock, 10_000, false);
  });

  it("follows the calendar on the finished tab until a different day is picked explicitly", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<GrainPage />);
    await user.click(screen.getByRole("tab", { name: "Завершённые" }));

    // Полночь на долгоживущей вкладке: день по умолчанию сменяется сам.
    localDayMock.mockReturnValue("2026-09-07");
    rerender(<GrainPage />);
    expect(screen.getByLabelText("День")).toHaveValue("2026-09-07");
    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        "/grain/wagons/?scope=finished&direction=intake&date_from=2026-09-07&date_to=2026-09-07",
      ),
    );

    // Явно выбранный чужой день закреплён и полночь его не трогает.
    const day = screen.getByLabelText("День");
    await user.clear(day);
    await user.type(day, "2026-09-04");
    localDayMock.mockReturnValue("2026-09-08");
    rerender(<GrainPage />);
    expect(screen.getByLabelText("День")).toHaveValue("2026-09-04");
    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        "/grain/wagons/?scope=finished&direction=intake&date_from=2026-09-04&date_to=2026-09-04",
      ),
    );

    // Выбор сегодняшней даты возвращает режим «за календарём».
    await user.clear(day);
    await user.type(day, "2026-09-08");
    localDayMock.mockReturnValue("2026-09-09");
    rerender(<GrainPage />);
    expect(screen.getByLabelText("День")).toHaveValue("2026-09-09");
    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        "/grain/wagons/?scope=finished&direction=intake&date_from=2026-09-09&date_to=2026-09-09",
      ),
    );
  });

  it("gates the table on the first load and keeps it during polling", () => {
    pagedApiMock.mockReturnValue({ ...emptyList, loading: true });
    const { rerender } = render(<GrainPage />);

    expect(screen.getByText("Загрузка…")).toBeInTheDocument();
    expect(screen.queryByTestId("wagon-table")).not.toBeInTheDocument();

    const wagon = { id: 1, number: "123 ABC", direction: "intake", status: "arrived" };
    pagedApiMock.mockReturnValue({ ...emptyList, items: [wagon], count: 1, loading: true });
    rerender(<GrainPage />);
    expect(screen.queryByText("Загрузка…")).not.toBeInTheDocument();
    expect(screen.getByTestId("wagon-table")).toBeInTheDocument();

    pagedApiMock.mockReturnValue(emptyList);
    rerender(<GrainPage />);
    expect(screen.getByTestId("wagon-table")).toHaveTextContent("На территории нет поездов на приём");
  });

  it("requests a picked day for finished export trips", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    await user.click(screen.getByRole("tab", { name: "Вывоз" }));
    await user.click(screen.getByRole("tab", { name: "Завершённые" }));
    const day = screen.getByLabelText("День");
    await user.clear(day);
    await user.type(day, "2026-09-04");

    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        "/grain/passages/?scope=finished&direction=passage&date_from=2026-09-04&date_to=2026-09-04",
      ),
    );
  });

  it("passes the search text to the on-site list without a day filter", async () => {
    const user = userEvent.setup();
    render(<GrainPage />);

    const search = screen.getByLabelText("Поиск");
    expect(search).toHaveAttribute("placeholder", "Номер, груз, поставщик");
    await user.type(search, "Колос");

    await waitFor(() =>
      expect(lastWagonsUrl()).toBe(
        "/grain/wagons/?scope=on_site&direction=intake&search=%D0%9A%D0%BE%D0%BB%D0%BE%D1%81",
      ),
    );
    expect(pagedApiMock).toHaveBeenCalledWith(lastWagonsUrl(), 50);
    expect(screen.queryByLabelText("День")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Ожидаются" }));
    expect(screen.queryByLabelText("Поиск")).not.toBeInTheDocument();
  });
});
