import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CameraFeed } from "@/components/camera-wall";
import { ShippingTable, type ShippingTableCapabilities, type ShippingTableProps } from "./shipping-table";
import type { AiCountingHistory, AiCountingSession, Order } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());
const deleteMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock, delete: deleteMock, defaults: { baseURL: "https://crm.test/api" } },
  apiError: () => "Сервер не ответил",
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: null, loading: false, error: "", reload: vi.fn().mockResolvedValue(undefined) }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("@/components/shipping/shipping-row-detail", () => ({
  ShippingRowDetail: ({ order, session }: { order: { id: number } | null; session: { id: number } | null }) => (
    <div data-testid="row-detail">панель {order ? `заказа #${order.id}` : `сессии #${session?.id}`}</div>
  ),
}));

const camera: CameraFeed = {
  id: "camera-2",
  name: "Camera 2",
  zone: "Пост погрузки",
  src: "cam2",
  kind: "nvr-channel",
  online: true,
};

function order(overrides: Partial<Order>): Order {
  return {
    id: 1,
    client: 1,
    client_name: "Магнум",
    currency: "KZT",
    status: "confirmed",
    transport_type: "truck",
    truck_number: "123ABC02",
    items: [{ product: 1, product_label: "Мука 50 кг", quantity: 40 }],
    total_amount: "0.00",
    paid_total: "0.00",
    is_fully_paid: true,
    debt_override: false,
    created_at: "2026-08-15T00:00:00Z",
    ...overrides,
  };
}

function session(overrides: Partial<AiCountingSession>): AiCountingSession {
  return {
    id: 100,
    order_id: 12,
    order_client_name: "Магнум",
    order_truck_number: "123ABC02",
    camera: "cam2",
    status: "active",
    started_at: "2026-08-27T05:20:24Z",
    started_by_id: 1,
    started_by_name: "loader",
    can_stop: true,
    last_status: { total: 7 },
    ...overrides,
  };
}

const history: AiCountingHistory = {
  id: 7,
  order_id: 11,
  order_client_name: "Магнум",
  order_truck_number: "123ABC02",
  camera: "cam2",
  camera_name: "Пост погрузки",
  status: "finished",
  started_at: "2026-08-27T05:20:24Z",
  ended_at: "2026-08-27T06:20:24Z",
  started_by_id: 1,
  started_by_name: "loader",
  final_total: 40,
  last_status: { total: 40 },
  has_recording: true,
  recording_available_until: null,
};

const waiting = order({ id: 10, status: "confirmed" });
const waitingWagon = order({ id: 13, status: "confirmed", transport_type: "train", truck_number: "" });
const loaded = order({ id: 11, status: "loaded", bags_loaded: 40, loading_camera: "cam2" });
const loading = order({ id: 12, status: "loading", bags_loaded: 5, loading_camera: "cam2" });
const shipped = order({ id: 14, status: "shipped", bags_loaded: 40, shipped_at: "2026-08-27T07:00:00Z" });

const noCapabilities: ShippingTableCapabilities = {
  canLoad: false,
  canTrain: false,
  canShip: false,
  canRollback: false,
  canViewShipping: false,
  canOpenOrder: false,
};

const reloadOrders = vi.fn().mockResolvedValue(undefined);
const reloadSessions = vi.fn().mockResolvedValue(undefined);

function renderTable(
  overrides: Omit<Partial<ShippingTableProps>, "capabilities"> & { capabilities?: Partial<ShippingTableCapabilities> },
) {
  const { capabilities, ...rest } = overrides;
  render(
    <ShippingTable
      orders={[waiting, loaded, loading, shipped]}
      sessions={[session({})]}
      histories={[]}
      camerasBySrc={new Map([[camera.src as string, camera]])}
      capabilities={{ ...noCapabilities, ...capabilities }}

      completedOrdersDays={1}
      reloadOrders={reloadOrders}
      reloadSessions={reloadSessions}
      {...rest}
    />,
  );
}

function rowOf(orderId: number) {
  return screen.getByText(`#${orderId}`).closest("tr") as HTMLTableRowElement;
}

describe("ShippingTable", () => {
  it("shows full wagon identifiers for orders and independently visible sessions", () => {
    renderTable({
      orders: [order({ id: 45, transport_type: "train", truck_number: "00123456" })],
      sessions: [session({ order_transport_type: "train", order_truck_number: "00012345" })],
    });
    expect(screen.getByText("Вагон 00123456")).toBeInTheDocument();
    expect(screen.getByText("Вагон 00012345")).toBeInTheDocument();
    expect(screen.queryByText("KZ")).not.toBeInTheDocument();
  });

  beforeEach(() => {
    postMock.mockReset();
    deleteMock.mockReset();
    pushMock.mockReset();
    reloadOrders.mockClear();
    reloadSessions.mockClear();
  });

  it("groups the queue by operator attention and keeps the mandatory groups visible", () => {
    renderTable({ orders: [waiting], sessions: [] });

    const headers = screen.getAllByRole("row").map((row) => row.textContent ?? "");
    expect(headers.some((text) => text.startsWith("На погрузке · 0"))).toBe(true);
    expect(headers.some((text) => text.startsWith("Готовы к выезду · 0"))).toBe(true);
    expect(headers.some((text) => text.startsWith("Ожидают погрузки · 1"))).toBe(true);
    expect(screen.queryByText(/Выехали/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Пусто")).toHaveLength(2);
  });

  it("waits for automatic acquisition and requires an explicit row expansion", async () => {
    const user = userEvent.setup();
    renderTable({ capabilities: { canLoad: true } });

    expect(within(rowOf(10)).getByText("Ожидает распознавания номера")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Начать погрузку" })).not.toBeInTheDocument();
    expect(within(rowOf(11)).getByText("Ожидает оформления выезда")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить выезд" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Действия: заказ #11/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("row-detail")).not.toBeInTheDocument();
    await user.click(within(rowOf(12)).getByRole("button", { name: "Раскрыть заказ #12" }));
    expect(screen.getByTestId("row-detail")).toHaveTextContent("панель заказа #12");
    expect(within(rowOf(12)).getByRole("button", { name: "Свернуть заказ #12" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("shows only states and the counting history to shipping.view", async () => {
    const user = userEvent.setup();
    renderTable({ capabilities: { canViewShipping: true }, histories: [history] });

    expect(within(rowOf(10)).getByText("Ожидает распознавания номера")).toBeInTheDocument();
    expect(within(rowOf(12)).getByText("Идёт погрузка")).toBeInTheDocument();
    expect(within(rowOf(11)).getByText("Ожидает оформления выезда")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Начать погрузку|Завершить погрузку|Оформить выезд)$/ })).toBeNull();
    expect(screen.getByText(/Выехали · сегодня/)).toBeInTheDocument();
    expect(within(rowOf(11)).getByRole("button", { name: "камера: 40" })).toBeInTheDocument();

    await user.click(within(rowOf(11)).getByRole("button", { name: /Действия: заказ #11/ }));
    expect(screen.getByRole("menuitem", { name: "История подсчёта" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Открыть заказ" })).not.toBeInTheDocument();
  });

  it("waits for automatic recognition for both wagons and trucks", () => {
    renderTable({ orders: [waiting, waitingWagon], capabilities: { canTrain: true } });

    expect(within(rowOf(13)).getByText("Ожидает распознавания номера")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Начать загрузку вагона" })).not.toBeInTheDocument();
    expect(within(rowOf(10)).getByText("Ожидает распознавания номера")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Начать погрузку" })).not.toBeInTheDocument();
  });

  it("disables finishing a loading that someone else's session owns", () => {
    renderTable({
      capabilities: { canLoad: true },
      sessions: [session({ can_stop: false, started_by_name: "Айдос" })],
    });

    const finish = within(rowOf(12)).getByRole("button", { name: "Завершить погрузку" });
    expect(finish).toBeDisabled();
    // Причина блокировки видна текстом: на планшете подсказки по наведению нет.
    expect(within(rowOf(12)).getByText("сессию запустил Айдос")).toBeInTheDocument();
  });

  it("expands a row from its cells but not from its buttons", async () => {
    const user = userEvent.setup();
    renderTable({ capabilities: { canLoad: true } });

    expect(screen.queryByTestId("row-detail")).not.toBeInTheDocument();
    await user.click(within(rowOf(12)).getByRole("button", { name: "Завершить погрузку" }));
    expect(screen.queryByTestId("row-detail")).not.toBeInTheDocument();
    const dialog = screen.getByRole("dialog", { name: "Завершить погрузку?" });
    expect(dialog).toHaveTextContent("зафиксировано 7 из 40 меш.");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));

    await user.click(within(rowOf(12)).getByText("Магнум"));
    expect(screen.getByTestId("row-detail")).toHaveTextContent("панель заказа #12");
    // Confirmed orders have nothing to expand.
    await user.click(within(rowOf(10)).getByText("Магнум"));
    expect(screen.getByTestId("row-detail")).toHaveTextContent("панель заказа #12");
  });

  it("posts the exit of a loaded order after confirmation", async () => {
    const user = userEvent.setup();
    postMock.mockResolvedValue({ data: {} });
    renderTable({ capabilities: { canShip: true } });

    await user.click(within(rowOf(11)).getByRole("button", { name: "Оформить выезд" }));
    const dialog = screen.getByRole("dialog", { name: "Оформить выезд?" });
    await user.click(within(dialog).getByRole("button", { name: "Подтвердить выезд" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/orders/11/ship/", {}));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(reloadOrders).toHaveBeenCalled();
    expect(reloadSessions).toHaveBeenCalled();
  });

  it("keeps a failed exit inside the confirmation", async () => {
    const user = userEvent.setup();
    postMock.mockRejectedValueOnce(new Error("boom"));
    renderTable({ capabilities: { canShip: true } });

    await user.click(within(rowOf(11)).getByRole("button", { name: "Оформить выезд" }));
    await user.click(screen.getByRole("button", { name: "Подтвердить выезд" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Сервер не ответил");
    expect(screen.getByRole("dialog", { name: "Оформить выезд?" })).toBeInTheDocument();
  });

  it("renders the day and search controls of the header filter", async () => {
    const user = userEvent.setup();
    const onDayChange = vi.fn();
    const onSearchChange = vi.fn();
    renderTable({
      filter: { day: "", today: "2026-09-06", search: "", appliedSearch: "", onDayChange, onSearchChange },
    });

    expect(screen.getByText("Очередь отгрузки")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Номер, клиент или № заказа")).toBe(screen.getByLabelText("Поиск"));
    expect(screen.getByLabelText("День")).toHaveValue("2026-09-06");
    expect(screen.getByLabelText("День")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Сегодня" })).toBeDisabled();
    expect(screen.queryByText(/Показан день/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("День"), { target: { value: "2026-09-05" } });
    expect(onDayChange).toHaveBeenCalledWith("2026-09-05");
    await user.type(screen.getByLabelText("Поиск"), "3");
    expect(onSearchChange).toHaveBeenCalledWith("3");
  });

  it("marks another day as a view without automatic row expansion", async () => {
    const user = userEvent.setup();
    const onDayChange = vi.fn();
    renderTable({
      capabilities: { canLoad: true },
      filter: {
        day: "2026-09-05",
        today: "2026-09-06",
        search: "",
        appliedSearch: "",
        onDayChange,
        onSearchChange: vi.fn(),
      },
    });

    expect(screen.getByText("Показан день 05.09.2026")).toBeInTheDocument();
    expect(screen.getByText(/^Выехали · 1$/)).toBeInTheDocument();
    expect(screen.queryByTestId("row-detail")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Сегодня" }));
    expect(onDayChange).toHaveBeenCalledWith("");
  });

  it("labels an applied search as results, drops the day line and locks the day", () => {
    renderTable({
      orders: [shipped],
      sessions: [],
      capabilities: { canViewShipping: true },
      filter: {
        day: "2026-09-05",
        today: "2026-09-06",
        search: "327",
        appliedSearch: "327",
        onDayChange: vi.fn(),
        onSearchChange: vi.fn(),
      },
    });

    expect(screen.getByText("Результаты поиска")).toBeInTheDocument();
    expect(screen.queryByText(/Показан день/)).not.toBeInTheDocument();
    expect(screen.getByText(/Выехали · за 30 дн\./)).toBeInTheDocument();
    expect(screen.getByLabelText("День")).toBeDisabled();
  });

  it("shows the search empty state only for the applied query", () => {
    renderTable({
      orders: [],
      sessions: [],
      filter: {
        day: "",
        today: "2026-09-06",
        search: "327",
        appliedSearch: "327",
        onDayChange: vi.fn(),
        onSearchChange: vi.fn(),
      },
    });

    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("keeps the queue header while typed text is not yet applied", () => {
    renderTable({
      orders: [],
      sessions: [],
      filter: {
        day: "",
        today: "2026-09-06",
        search: "32",
        appliedSearch: "",
        onDayChange: vi.fn(),
        onSearchChange: vi.fn(),
      },
    });

    // Поле управляется набранным текстом, а шапка и пустое состояние — тем,
    // что реально запрошено: до задержки ввода это ещё очередь.
    expect(screen.getByLabelText("Поиск")).toHaveValue("32");
    expect(screen.getByText("Очередь отгрузки")).toBeInTheDocument();
    expect(screen.queryByText("Результаты поиска")).not.toBeInTheDocument();
    expect(screen.getByText("Нет заказов на посту")).toBeInTheDocument();
    expect(screen.getByLabelText("День")).toBeEnabled();
  });

  it("renders a session without an accessible order as its own loading row", () => {
    renderTable({
      orders: [waiting],
      sessions: [session({ id: 200, order_id: 999, order_client_name: "Чужой отдел" })],
      capabilities: { canLoad: true },
    });

    const row = rowOf(999);
    expect(within(row).getByText("нет доступа к заказу")).toBeInTheDocument();
    expect(within(row).getByText("7 / —")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Завершить погрузку" })).toBeInTheDocument();
  });

  it("allows train staff to finish an automatic wagon session and labels its source", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ data: {} });
    renderTable({
      orders: [],
      sessions: [
        session({
          automatically_started: true,
          order_transport_type: "train",
          started_by_id: null,
          started_by_name: "",
        }),
      ],
      capabilities: { canTrain: true },
    });
    expect(within(rowOf(12)).getAllByText(/Автоматически/).length).toBeGreaterThan(0);
    await user.click(within(rowOf(12)).getByRole("button", { name: "Завершить погрузку" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Завершить погрузку" }));
    await waitFor(() =>
      expect(deleteMock).toHaveBeenCalledWith("/cameras/cam2/ai/", {
        params: { order_id: 12, session_id: 100, complete_order: 1 },
        data: { order_id: 12, session_id: 100, complete_order: true },
      }),
    );
    expect(postMock).not.toHaveBeenCalled();
  });

  it("keeps saved transport details accessible after loading and departure", async () => {
    const user = userEvent.setup();
    renderTable({ orders: [loaded, shipped], sessions: [], capabilities: { canLoad: true } });
    await user.click(within(rowOf(11)).getByRole("button", { name: "Раскрыть заказ #11" }));
    expect(screen.getByRole("region", { name: "Распознанный транспорт" })).toBeInTheDocument();
    expect(screen.queryByTestId("row-detail")).not.toBeInTheDocument();
    await user.click(within(rowOf(14)).getByRole("button", { name: "Раскрыть заказ #14" }));
    expect(screen.getAllByRole("region", { name: "Распознанный транспорт" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Начать погрузку|Завершить погрузку/ })).not.toBeInTheDocument();
  });
});
