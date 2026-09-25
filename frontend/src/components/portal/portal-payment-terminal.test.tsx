import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { PortalOrder } from "@/lib/types";
import { PortalPaymentCard } from "./portal-payment-terminal";

function order(overrides: Partial<PortalOrder> = {}): PortalOrder {
  return {
    id: 642,
    status: "shipped",
    payment_status: "unpaid",
    settlement_intent: "pending",
    payment_method: "pending",
    currency: "KZT",
    transport_type: "truck",
    store: null,
    store_name: null,
    items: [],
    total_amount: "50000.00",
    paid_total: "0.00",
    remaining_amount: "50000.00",
    has_pending_payment: false,
    available_amount: "50000.00",
    payment_parts: [],
    client_phone: "+7 (705) 565-65-65",
    receipt_available: false,
    truck_number: "",
    debt_requested: false,
    created_at: "2026-09-17T08:00:00Z",
    ...overrides,
  };
}

function renderCard(overrides: Partial<PortalOrder> = {}) {
  const onPay = vi.fn().mockResolvedValue(undefined);
  render(<PortalPaymentCard order={order(overrides)} busy={false} onPay={onPay} onRelease={vi.fn()} />);
  return onPay;
}

describe("оплата в кабинете как POS-терминал", () => {
  it("показывает остаток крупно и только способы Kaspi — без наличных и «в долг»", () => {
    renderCard();
    expect(screen.getByText("Остаток к оплате")).toBeInTheDocument();
    expect(screen.getByLabelText("Сумма оплаты")).toHaveTextContent("50 000 ₸");
    expect(screen.getByRole("button", { name: /Kaspi QR/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Счёт в Kaspi/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Наличными/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /В долг/ })).not.toBeInTheDocument();
  });

  it("набирает сумму на клавиатуре и сразу создаёт QR на неё", async () => {
    const user = userEvent.setup();
    const onPay = renderCard();
    await user.click(screen.getByRole("button", { name: "Стереть" }));
    expect(screen.getByLabelText("Сумма оплаты")).toHaveTextContent("5 000 ₸");
    await user.click(screen.getByRole("button", { name: "Цифра 0" }));
    await user.click(screen.getByRole("button", { name: "Стереть" }));
    await user.click(screen.getByRole("button", { name: "Стереть" }));
    await user.click(screen.getByRole("button", { name: "Цифра 5" }));
    expect(screen.getByLabelText("Сумма оплаты")).toHaveTextContent("5 005 ₸");
    await user.click(screen.getByRole("button", { name: /Весь остаток · 50\s000\s₸/ }));
    expect(screen.getByLabelText("Сумма оплаты")).toHaveTextContent("50 000 ₸");
    await user.click(screen.getByRole("button", { name: /Kaspi QR/ }));
    expect(onPay).toHaveBeenCalledWith("kaspi", "50000");
  });

  it("не даёт оплатить больше доступного", async () => {
    const user = userEvent.setup();
    renderCard({ available_amount: "100.00", remaining_amount: "100.00", total_amount: "100.00" });
    await user.click(screen.getByRole("button", { name: "Цифра 5" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Доступно не более 100");
    expect(screen.getByRole("button", { name: /Kaspi QR/ })).toBeDisabled();
  });

  it("отправляет счёт в Kaspi на подставленный телефон клиента", async () => {
    const user = userEvent.setup();
    const onPay = renderCard();
    await user.click(screen.getByRole("button", { name: /Счёт в Kaspi/ }));
    expect(screen.getByLabelText("Телефон для счёта в Kaspi")).toHaveValue("+7 (705) 565-65-65");
    await user.click(screen.getByRole("button", { name: /Отправить счёт · 50\s000\s₸/ }));
    expect(onPay).toHaveBeenCalledWith("invoice", "50000", "+7 (705) 565-65-65");
  });

  it("принимает остаток в тиынах целиком", async () => {
    const user = userEvent.setup();
    const onPay = renderCard({ available_amount: "0.01", remaining_amount: "0.01" });
    await user.click(screen.getByRole("button", { name: /Kaspi QR/ }));
    expect(onPay).toHaveBeenCalledWith("kaspi", "0.01");
  });

  it("для заказа в долларах не предлагает онлайн-оплату", () => {
    renderCard({ currency: "USD" });
    expect(screen.getByText(/Оплата в долларах — в кассе ASYL/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Kaspi QR/ })).not.toBeInTheDocument();
  });
});
