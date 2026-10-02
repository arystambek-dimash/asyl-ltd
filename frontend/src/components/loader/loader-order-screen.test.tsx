import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { LoaderOrder } from "@/lib/loader";
import type { TransportPair } from "@/lib/plates";
import { makeLoaderItem } from "@/test-utils/factories";
import { LoaderOrderScreen, LoaderShippedScreen } from "./loader-order-screen";

const wagonOrder: LoaderOrder = {
  id: 625,
  status: "confirmed",
  transport_type: "train",
  truck_number: "",
  currency: "KZT",
  planned_on: "2026-09-24",
  client_name: "ТОО Нур",
  items: [makeLoaderItem({ label: "Мука в/с", quantity: 1360, weight_kg: "68000" })],
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

const truckOrder: LoaderOrder = {
  ...wagonOrder,
  transport_type: "truck",
  truck_number: "403BJN13",
  items: [makeLoaderItem({ quantity: 20, weight_kg: "1000" })],
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

describe("что и кому грузить", () => {
  const itemRows = () => within(screen.getByRole("list", { name: "Товары" })).getAllByRole("listitem");

  it("клиент — крупным заголовком карточки", () => {
    render(<LoaderOrderScreen {...screenProps} busy={false} />);
    expect(screen.getByRole("heading", { name: "ТОО Нур" })).toBeInTheDocument();
  });

  it("один товар — тоже строкой: полное название и мешки", () => {
    render(<LoaderOrderScreen {...screenProps} busy={false} />);
    const rows = itemRows();

    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Д1с · Красный 50 кг");
    expect(within(rows[0]).getByText("20 мешков")).toBeInTheDocument();
  });

  it("каждый товар — своей строкой, без обрезки", () => {
    const order: LoaderOrder = {
      ...truckOrder,
      items: [
        makeLoaderItem({ label: "Первый сорт DIKHAN BABA NAN 50кг · Красный 50 кг", quantity: 5 }),
        makeLoaderItem({ label: "Высший сорт · Зелёный 50 кг", quantity: 12, color: "Green" }),
      ],
      bags: 17,
    };
    render(<LoaderOrderScreen {...screenProps} order={order} busy={false} />);
    const rows = itemRows();

    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("5 мешков")).toBeInTheDocument();
    expect(within(rows[1]).getByText("12 мешков")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Товары" }).querySelector(".truncate")).toBeNull();
  });
});

describe("кнопка отгрузки", () => {
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

describe("экран «Отгрузка подтверждена»", () => {
  const shipped = (fields: Partial<LoaderOrder>) =>
    render(
      <LoaderShippedScreen
        order={{ ...wagonOrder, status: "shipped", shipped_at: "2026-10-02T11:31:00+05:00", ...fields }}
        busy={false}
        onPrint={vi.fn()}
        onBack={vi.fn()}
        onUndo={vi.fn()}
      />,
    );

  it("у фуры — «Скопировать отчёт» для чата отгрузок", () => {
    shipped({ transport_type: "truck", truck_number: "909ERD13", total_kg: "1000" });

    expect(screen.getByRole("button", { name: "Скопировать отчёт" })).toBeEnabled();
  });

  it("у вагона копии нет: его отчёт отправляет бот", () => {
    shipped({});

    expect(screen.queryByRole("button", { name: /Скопировать/ })).not.toBeInTheDocument();
  });
});
