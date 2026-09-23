import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PortalOrder } from "@/lib/types";

import PortalOrderDetail from "./page";

const mocks = vi.hoisted(() => ({
  order: null as PortalOrder | null,
  reload: vi.fn(),
  setData: vi.fn(),
  setTruck: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: mocks.order, loading: false, error: "", reload: mocks.reload, setData: mocks.setData }),
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: vi.fn() }));
vi.mock("@/lib/portal-actions", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/portal-actions")>()),
  setTruck: mocks.setTruck,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

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
    apipay_invoice: null,
    client_phone: "",
    client_country: "Кыргызстан",
    receipt_available: false,
    truck_number: "",
    trailer_number: "",
    transport_locked: false,
    debt_requested: false,
    debt_override: false,
    created_at: "2026-09-23T10:00:00+05:00",
    ...fields,
  }) as PortalOrder;

async function renderPage() {
  const params = Promise.resolve({ id: "5" });
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <PortalOrderDetail params={params} />
      </Suspense>,
    );
  });
}

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
