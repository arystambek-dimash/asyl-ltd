import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { LoaderOrder } from "@/lib/loader";
import type { TransportPair } from "@/lib/plates";
import { LoaderOrderScreen } from "./loader-order-screen";

const wagonOrder: LoaderOrder = {
  id: 625,
  status: "confirmed",
  transport_type: "train",
  truck_number: "",
  currency: "KZT",
  planned_on: "2026-09-24",
  client_name: "ТОО Нур",
  items: [{ label: "Мука в/с", quantity: 1360, weight_kg: "68000", unit_price: null }],
  bags: 1360,
  total_kg: "68000",
  total_amount: "0",
  shipped_at: null,
  trailer_number: "",
  transport_suggestions: [],
  transport_locked: false,
  client_country: "KZ",
  payment_status: "unpaid",
  remaining_amount: "0.00",
  can_rollback: false,
  rail_station: "",
  wagons: [],
  report_sent_at: null,
  report_deliveries: [],
};

function Screen({ order }: { order: LoaderOrder }) {
  const [numbers, setNumbers] = useState<TransportPair>({ truck_number: order.truck_number, trailer_number: "" });
  return (
    <LoaderOrderScreen
      order={order}
      today="2026-09-24"
      canConfirm
      busy={false}
      error=""
      numbers={numbers}
      onNumbers={setNumbers}
      onBack={vi.fn()}
      onConfirm={vi.fn()}
      onPrint={vi.fn()}
      onShipByReport={vi.fn()}
    />
  );
}

describe("номер вагона у грузчика", () => {
  it("объясняет под полем, почему номер не примут", async () => {
    const user = userEvent.setup();
    render(<Screen order={wagonOrder} />);
    const wagon = screen.getByLabelText("Номер вагона");
    await user.type(wagon, "1234");
    expect(wagon).toHaveAccessibleDescription("Номер вагона: 8 цифр");
    await user.type(wagon, "567890");
    expect(wagon).toHaveValue("12345678");
    expect(wagon).not.toHaveAccessibleDescription();
  });

  it("номер, который указал клиент, не меняется", () => {
    render(<Screen order={{ ...wagonOrder, truck_number: "00123456", transport_locked: true }} />);
    expect(screen.getByLabelText("Номер вагона")).toBeDisabled();
    expect(screen.getByLabelText("Номер вагона")).toHaveValue("00123456");
    expect(screen.getByText(/Номер указал клиент/)).toBeInTheDocument();
  });
});

describe("кнопка отгрузки", () => {
  const truckOrder: LoaderOrder = {
    ...wagonOrder,
    transport_type: "truck",
    truck_number: "403BJN13",
    items: [{ label: "Д1с · Красный 50 кг", quantity: 20, weight_kg: "1000", unit_price: null }],
    bags: 20,
    total_kg: "1000",
  };
  const screenProps = {
    order: truckOrder,
    today: "2026-09-24",
    canConfirm: true,
    error: "",
    numbers: { truck_number: "403BJN13", trailer_number: "" },
    onNumbers: vi.fn(),
    onBack: vi.fn(),
    onConfirm: vi.fn(),
    onPrint: vi.fn(),
    onShipByReport: vi.fn(),
  };

  it("пока идёт работа, говорит, что именно: проверка складов или отгрузка", () => {
    const { rerender } = render(<LoaderOrderScreen {...screenProps} busy busyLabel="Проверяем склады…" />);
    expect(screen.getByRole("button", { name: /Проверяем склады…/ })).toBeDisabled();

    rerender(<LoaderOrderScreen {...screenProps} busy />);
    expect(screen.getByRole("button", { name: /Отгружаем…/ })).toBeDisabled();

    rerender(<LoaderOrderScreen {...screenProps} busy={false} />);
    expect(screen.getByRole("button", { name: /Подтвердить отгрузку/ })).toBeEnabled();
  });

  it("пока идёт работа, с экрана не уйти: «Назад» выключен", () => {
    const { rerender } = render(<LoaderOrderScreen {...screenProps} busy busyLabel="Проверяем склады…" />);
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();

    rerender(<LoaderOrderScreen {...screenProps} busy={false} />);
    expect(screen.getByRole("button", { name: "Назад" })).toBeEnabled();
  });
});
