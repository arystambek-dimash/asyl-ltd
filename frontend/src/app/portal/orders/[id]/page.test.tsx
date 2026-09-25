import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import type { PortalOrder } from "@/lib/types";

import { renderRoutePage } from "@/test-utils/route-page";
import PortalOrderDetail from "./page";

const mocks = vi.hoisted(() => ({
  order: null as PortalOrder | null,
  reload: vi.fn(),
  setData: vi.fn(),
  setTruck: vi.fn(),
  payOrder: vi.fn(),
  downloadReceipt: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: mocks.order, loading: false, error: "", reload: mocks.reload, setData: mocks.setData }),
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: vi.fn() }));
vi.mock("@/lib/portal-actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/portal-actions")>()),
  setTruck: mocks.setTruck,
  payOrder: mocks.payOrder,
  downloadReceipt: mocks.downloadReceipt,
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));

const order = (fields: Partial<PortalOrder> = {}): PortalOrder =>
  ({
    id: 5,
    status: "confirmed",
    payment_status: "unpaid",
    settlement_intent: "pending",
    payment_method: "pending",
    currency: "KZT",
    transport_type: "truck",
    store: null,
    store_name: null,
    items: [],
    total_amount: "100.00",
    paid_total: "0.00",
    remaining_amount: "100.00",
    has_pending_payment: false,
    available_amount: null,
    payment_parts: [],
    client_phone: "",
    client_country: "Кыргызстан",
    receipt_available: false,
    truck_number: "",
    trailer_number: "",
    transport_locked: false,
    debt_requested: false,
    created_at: "2026-09-23T10:00:00+05:00",
    ...fields,
  }) as PortalOrder;

function blobError(detail: string): AxiosError {
  const error = new AxiosError("failed");
  error.response = {
    status: 400,
    statusText: "",
    data: new Blob([JSON.stringify({ detail })], { type: "application/json" }),
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

const renderPage = () => renderRoutePage(PortalOrderDetail, "5");

describe("портал: номер машины", () => {
  beforeEach(() => {
    mocks.reload.mockReset();
    mocks.setData.mockReset();
    mocks.setTruck.mockReset();
  });

  it("клиент указывает тягач и прицеп, ответ сразу на экране", async () => {
    const user = userEvent.setup();
    mocks.order = order();
    const saved = order({ truck_number: "07KG695ADT", trailer_number: "07KG837PB" });
    mocks.setTruck.mockResolvedValue(saved);
    await renderPage();

    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
    await user.type(screen.getByLabelText("Тягач"), "07 kg 695 adt");
    await user.type(screen.getByLabelText("Прицеп (необязательно)"), "07kg837pb");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(mocks.setTruck).toHaveBeenCalledWith(5, { truck_number: "07KG695ADT", trailer_number: "07KG837PB" });
    expect(mocks.setData).toHaveBeenCalledWith(saved);
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("номер, указанный менеджером, только для чтения", async () => {
    mocks.order = order({ truck_number: "403BJN13", trailer_number: "07KG837PB", transport_locked: true });
    await renderPage();

    expect(screen.getByText(/Номер указал менеджер/)).toBeInTheDocument();
    expect(screen.getByText("403 BJN 13 / 07 KG 837 PB")).toBeInTheDocument();
    expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("у вагона — одно поле из 8 цифр", async () => {
    const user = userEvent.setup();
    mocks.order = order({ transport_type: "train" });
    mocks.setTruck.mockResolvedValue(order({ transport_type: "train", truck_number: "00123456" }));
    await renderPage();

    expect(screen.queryByLabelText("Прицеп (необязательно)")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Номер вагона"), "00123456");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(mocks.setTruck).toHaveBeenCalledWith(5, { truck_number: "00123456" });
  });
});

describe("портал: машина на территории", () => {
  it.each([
    ["arrived", "unpaid"],
    ["loading", "unpaid"],
    ["loaded", "unpaid"],
    ["shipped", "settled"],
  ])("в статусе %s номер машины клиенту не показывается", async (status, payment_status) => {
    mocks.order = order({
      status,
      payment_status,
      truck_number: "403BJN13",
      trailer_number: "07KG837PB",
      transport_locked: true,
    });
    await renderPage();

    expect(screen.queryByText(/403 BJN 13/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Машина:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Номер указал менеджер/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Тягач")).not.toBeInTheDocument();
  });

  it("у вагонного заказа вагоны отчёта остаются видны", async () => {
    mocks.order = order({
      status: "shipped",
      payment_status: "settled",
      transport_type: "train",
      truck_number: "00123456",
      transport_locked: true,
      rail_station: "Достык",
      wagons: [{ number: "28087658", product_label: "Мука", bags: 1360, weight_kg: "68000.00" }],
    } as Partial<PortalOrder>);
    await renderPage();

    expect(screen.queryByText(/Вагон:/)).not.toBeInTheDocument();
    expect(screen.getByText(/28087658/)).toBeInTheDocument();
  });
});

describe("портал: оплата", () => {
  it("ответ оплаты — заказ целиком: применяется без перечитывания", async () => {
    const user = userEvent.setup();
    mocks.reload.mockReset();
    mocks.setData.mockReset();
    mocks.order = order({ status: "shipped", available_amount: "100.00" });
    const paid = order({ status: "shipped", has_pending_payment: true });
    mocks.payOrder.mockResolvedValue(paid);
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Kaspi QR/ }));

    expect(mocks.payOrder).toHaveBeenCalledWith(5, "kaspi", { amount: "100", phone_number: undefined });
    expect(mocks.setData).toHaveBeenCalledWith(paid);
    expect(mocks.reload).not.toHaveBeenCalled();
  });
});

describe("портал: квитанция", () => {
  it("скачивание не перечитывает заказ", async () => {
    const user = userEvent.setup();
    mocks.reload.mockReset();
    mocks.setData.mockReset();
    mocks.order = order({ status: "shipped", payment_status: "settled", receipt_available: true });
    mocks.downloadReceipt.mockResolvedValue(undefined);
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Скачать квитанцию/ }));

    expect(mocks.downloadReceipt).toHaveBeenCalledWith(5);
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(mocks.setData).not.toHaveBeenCalled();
  });

  it("не скачалась — клиент видит причину сервера, а не общую ошибку", async () => {
    const user = userEvent.setup();
    mocks.order = order({ status: "shipped", payment_status: "settled", receipt_available: true });
    mocks.downloadReceipt.mockRejectedValue(blobError("Квитанция доступна после оплаты"));
    await renderPage();

    await user.click(screen.getByRole("button", { name: /Скачать квитанцию/ }));

    expect(mocks.downloadReceipt).toHaveBeenCalledWith(5);
    expect(await screen.findByText("Квитанция доступна после оплаты")).toBeInTheDocument();
  });
});
