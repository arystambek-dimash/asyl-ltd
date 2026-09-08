import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShippingTransportHistory } from "@/lib/types";
import { ShippingTransportEvidence } from "./shipping-transport-evidence";

const mocks = vi.hoisted(() => ({ data: [] as ShippingTransportHistory[], useApi: vi.fn(), reload: vi.fn() }));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) => {
    mocks.useApi(url);
    return { data: mocks.data, loading: false, error: "", reload: mocks.reload };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: vi.fn() }));
const evidence: ShippingTransportHistory = {
  id: 7,
  conveyor_camera: "cam2",
  number_camera: "cam12",
  recognition_model: "wagon_number",
  number: "00123456",
  first_seen_at: "2026-09-08T05:00:00Z",
  last_seen_at: "2026-09-08T06:00:00Z",
  status: "matched",
  order_id: 42,
  session_id: 8,
  image_url: "https://crm.test/api/cameras/shipping-transport/images/7/?token=signed",
};
beforeEach(() => {
  vi.clearAllMocks();
  mocks.data = [{ ...evidence }];
});

describe("ShippingTransportEvidence", () => {
  it("scopes evidence to the order and shows the saved image, number and time", () => {
    render(<ShippingTransportEvidence orderId={42} />);
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/shipping-transport/history/?order_id=42");
    expect(screen.getByText("Вагон 00123456")).toBeInTheDocument();
    expect(screen.getByText(/Первое появление:/)).toBeInTheDocument();
    expect(screen.getByText(/Последнее появление:/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Номер 00123456, камера cam12" })).toHaveAttribute(
      "src",
      evidence.image_url,
    );
    expect(screen.getByRole("link", { name: "Открыть кадр номера 00123456" })).toHaveAttribute(
      "href",
      evidence.image_url,
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows missing frames explicitly and replaces the scope when another order opens", () => {
    mocks.data = [{ ...evidence, image_url: null }];
    const { rerender } = render(<ShippingTransportEvidence orderId={42} />);
    expect(screen.getByText("Кадр недоступен")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    mocks.data = [];
    rerender(<ShippingTransportEvidence orderId={43} />);
    expect(mocks.useApi).toHaveBeenLastCalledWith("/cameras/shipping-transport/history/?order_id=43");
    expect(screen.getByText("Распознанных номеров пока нет.")).toBeInTheDocument();
  });

  it("shows persisted transport presence and a current alert inside order details", () => {
    mocks.data = [
      {
        ...evidence,
        visit_id: "visit-a",
        tracking_alert: "Транспорт сменился. Проверьте погрузку.",
        tracking: {
          schema_version: 1,
          basis: "transport_body",
          presence: "present",
          motion: "stationary",
          visit_id: "visit-a",
          observed_at: "2026-09-08T05:03:00Z",
          present_since: "2026-09-08T05:00:00Z",
          last_seen_at: "2026-09-08T05:03:00Z",
          stationary_since: "2026-09-08T05:01:00Z",
          absent_since: null,
          detection_count: 1,
          reason: "",
          number_associated: true,
        },
      },
    ];
    render(<ShippingTransportEvidence orderId={42} />);
    expect(screen.getByText("На момент наблюдения")).toBeInTheDocument();
    expect(screen.getByText("Транспорт стоит")).toBeInTheDocument();
    expect(screen.getByText(/Стоит с:/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Транспорт сменился");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows a body-only tracking alert without an empty number or broken image label", () => {
    mocks.data = [{ ...evidence, status: "tracking_alert", number: "" }];
    render(<ShippingTransportEvidence orderId={42} />);
    expect(screen.getByRole("article", { name: "Номер не распознан" })).toBeInTheDocument();
    expect(screen.getByText("Номер не распознан")).toBeInTheDocument();
    expect(screen.getByText("Проверьте транспорт")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть кадр транспорта" })).toHaveAttribute("href", evidence.image_url);
    expect(screen.getByRole("img", { name: "Транспорт, камера cam12" })).toBeInTheDocument();
    expect(screen.queryByText("Заказ найден")).not.toBeInTheDocument();
  });

  it("shows an observed number saved without claiming an order match", () => {
    mocks.data = [{ ...evidence, status: "observed", order_id: null, session_id: null }];
    render(<ShippingTransportEvidence conveyorCamera="cam2" />);
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/shipping-transport/history/?conveyor_camera=cam2");
    expect(screen.getByText("Вагон 00123456")).toBeInTheDocument();
    expect(screen.getByText("Номер зафиксирован")).toBeInTheDocument();
    expect(screen.queryByText("Заказ найден")).not.toBeInTheDocument();
    expect(screen.queryByText(/Заказ #/)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows the current auto-finish wait once above order evidence and preserves its completed snapshot", () => {
    const auto_finish = {
      state: "waiting" as const,
      remaining_seconds: 24,
      observed_at: "2026-09-08T05:00:00Z",
      detail: "Зона свободна, конвейер пуст.",
    };
    mocks.data = [
      { ...evidence, auto_finish },
      { ...evidence, id: 8, auto_finish },
    ];
    const { rerender } = render(<ShippingTransportEvidence orderId={42} liveAutoFinish />);
    expect(screen.getAllByRole("group", { name: "Автозавершение погрузки" })).toHaveLength(1);
    expect(screen.getByText("Автозавершение через 24 с")).toBeInTheDocument();
    mocks.data = [{ ...evidence, auto_finish: { ...auto_finish, state: "completed", remaining_seconds: null } }];
    rerender(<ShippingTransportEvidence orderId={42} />);
    expect(screen.getByText("Погрузка завершена автоматически")).toBeInTheDocument();
    expect(screen.getByText(/Зафиксировано:/)).toBeInTheDocument();
  });
});
