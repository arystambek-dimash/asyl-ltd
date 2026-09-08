import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShippingTransportAutomation, ShippingTransportHistory } from "@/lib/types";
import { ShippingTransportStatus } from "./shipping-transport-status";

const mocks = vi.hoisted(() => ({
  data: [] as ShippingTransportAutomation[],
  evidence: [] as ShippingTransportHistory[],
  error: "",
  reload: vi.fn().mockResolvedValue(undefined),
  useApi: vi.fn(),
  polling: vi.fn(),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.useApi(url);
    return {
      data: url?.includes("/history/") ? mocks.evidence : mocks.data,
      loading: false,
      error: mocks.error,
      reload: mocks.reload,
    };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.polling }));

const status: ShippingTransportAutomation = {
  conveyor_camera: "cam2",
  number_camera: "cam12",
  recognition_model: "wagon_number",
  state: "no_order",
  detail: "Заказ не найден. Номер и снимок сохранены.",
  number: "00123456",
  observed_at: "2026-09-08T05:00:00Z",
  order_id: null,
  session_id: null,
};
function panel(enabled = true) {
  return <ShippingTransportStatus enabled={enabled} camerasBySrc={new Map()} />;
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.data = [{ ...status }];
  mocks.evidence = [];
  mocks.error = "";
});

describe("ShippingTransportStatus", () => {
  it("polls server state and never offers manual selection or a camera launcher", () => {
    render(panel());
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/shipping-transport/");
    expect(mocks.polling).toHaveBeenCalledWith(mocks.reload, 3_000, true);
    expect(screen.getByText("Вагон 00123456")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Привязать|Начать|Запустить/ })).not.toBeInTheDocument();
    expect(mocks.useApi.mock.calls.some(([url]) => url?.includes("/history/"))).toBe(false);
  });

  it("opens saved evidence only inside the unresolved conveyor details", async () => {
    const user = userEvent.setup();
    render(panel());
    const card = screen.getByRole("group", { name: "Привязка: cam2" });
    await user.click(within(card).getByRole("button", { name: "Сведения о транспорте" }));
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/shipping-transport/history/?conveyor_camera=cam2");
    expect(within(card).getByRole("region", { name: "Распознанный транспорт" })).toBeInTheDocument();
    expect(screen.queryByText(/Журнал/)).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Сведения о транспорте" }));
    expect(screen.queryByRole("region", { name: "Распознанный транспорт" })).not.toBeInTheDocument();
  });

  it("shows automatically assigned order and gates requests by access", () => {
    mocks.data = [{ ...status, state: "loading", order_id: 42, session_id: 8 }];
    const { rerender } = render(panel());
    expect(screen.getByText("Идёт погрузка")).toBeInTheDocument();
    expect(screen.getByText("· заказ #42")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сведения о транспорте" })).not.toBeInTheDocument();
    rerender(panel(false));
    expect(mocks.useApi).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("keeps unresolved transport evidence accessible after the vehicle leaves", () => {
    mocks.data = [{ ...status, state: "waiting_number", number: null, observed_at: null }];
    render(panel());
    expect(screen.getByRole("button", { name: "Сведения о транспорте" })).toBeInTheDocument();
  });

  it("keeps counting active when transport disappears and shows the operator alert", () => {
    mocks.data = [
      {
        ...status,
        state: "loading",
        order_id: 42,
        tracking_alert: "Транспорт не обнаружен. Проверьте погрузку.",
        tracking: {
          schema_version: 1,
          basis: "transport_body",
          presence: "absent",
          motion: "unknown",
          visit_id: "visit-a",
          observed_at: "2026-09-08T05:03:00Z",
          present_since: "2026-09-08T05:00:00Z",
          last_seen_at: "2026-09-08T05:02:00Z",
          stationary_since: null,
          absent_since: "2026-09-08T05:03:00Z",
          detection_count: 0,
          reason: "",
          number_associated: true,
        },
      },
    ];
    render(panel());
    expect(screen.getByText("Идёт погрузка")).toBeInTheDocument();
    expect(screen.getByText("Транспорт не обнаружен")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Проверьте погрузку");
    expect(screen.queryByText("Погрузка завершена")).not.toBeInTheDocument();
  });

  it("shows automatic finish gating while the loading session stays active", () => {
    mocks.data = [
      {
        ...status,
        state: "loading",
        order_id: 42,
        auto_finish: {
          state: "blocked",
          remaining_seconds: null,
          observed_at: "2026-09-08T05:00:00Z",
          detail: "Кадр конвейера недоступен. Отсчёт сброшен.",
        },
      },
    ];
    render(panel());
    expect(screen.getByText("Идёт погрузка")).toBeInTheDocument();
    expect(screen.getByText("Автозавершение приостановлено")).toBeInTheDocument();
    expect(screen.getByText("Кадр конвейера недоступен. Отсчёт сброшен.")).toBeInTheDocument();
    expect(screen.queryByText("Погрузка завершена")).not.toBeInTheDocument();
  });
});
