import { render, screen, waitFor, within } from "@testing-library/react";
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
  isKiosk: false,
  kioskCamera: null,
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
      monoblockCameras={[camera as CameraFeed & { src: string }]}
      cameraOwners={{ cam2: 12 }}
      continuousReady
      continuousDetail=""
      cameraLocked={false}
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

  it("gives the kiosk its launcher, hides exit paperwork and auto-expands its own loading", () => {
    renderTable({ capabilities: { canLoad: true, isKiosk: true, kioskCamera: "cam2" } });

    expect(within(rowOf(10)).getByRole("button", { name: "Начать погрузку" })).toBeInTheDocument();
    expect(within(rowOf(11)).getByText("Ожидает оформления выезда")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить выезд" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Действия: заказ #11/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("row-detail")).toHaveTextContent("панель заказа #12");
    expect(within(rowOf(12)).getByRole("button", { name: "Свернуть заказ #12" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("shows only states and the counting history to shipping.view", async () => {
    const user = userEvent.setup();
    renderTable({ capabilities: { canViewShipping: true }, histories: [history] });

    expect(within(rowOf(10)).getByText("Ожидает запуска")).toBeInTheDocument();
    expect(within(rowOf(12)).getByText("Идёт погрузка")).toBeInTheDocument();
    expect(within(rowOf(11)).getByText("Ожидает оформления выезда")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Начать погрузку|Завершить погрузку|Оформить выезд)$/ })).toBeNull();
    expect(screen.getByText(/Выехали · сегодня/)).toBeInTheDocument();
    expect(within(rowOf(11)).getByRole("button", { name: "камера: 40" })).toBeInTheDocument();

    await user.click(within(rowOf(11)).getByRole("button", { name: /Действия: заказ #11/ }));
    expect(screen.getByRole("menuitem", { name: "История подсчёта" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Открыть заказ" })).not.toBeInTheDocument();
  });

  it("offers wagon start only to train.load and keeps trucks waiting", () => {
    renderTable({ orders: [waiting, waitingWagon], capabilities: { canTrain: true } });

    expect(within(rowOf(13)).getByRole("button", { name: "Начать загрузку вагона" })).toBeInTheDocument();
    expect(within(rowOf(10)).getByText("Ожидает запуска")).toBeInTheDocument();
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
});
