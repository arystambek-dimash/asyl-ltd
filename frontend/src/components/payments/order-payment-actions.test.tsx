import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderPaymentActions, paymentAmountProblem } from "./order-payment-actions";
import type { Me, Order } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка оплаты",
}));

const me = { id: 1, username: "cashier", permissions: ["payments.create"], is_superuser: false } as unknown as Me;

const order = {
  id: 156,
  client_name: "Ерхан Тетрадь",
  client_phone: "87001234567",
  currency: "KZT",
  status: "shipped",
  total_amount: "707000",
  paid_total: "0",
  remaining_amount: "707000",
  payment_status: "unpaid",
  payments: [],
  pending_payments: [],
} as unknown as Order;

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ data: {} });
});

describe("OrderPaymentActions", () => {
  it("offers only receiving money and a remote invoice", () => {
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Запросить оплату/ })).not.toBeInTheDocument();
  });

  it("receives cash for the full remainder by default", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OrderPaymentActions order={order} me={me} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    expect(screen.getByLabelText("Сумма")).toHaveValue(707000);
    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "cash",
      stage: "received",
    });
    expect(onChanged).toHaveBeenCalledWith(expect.stringContaining("долг уменьшен"));
  });

  it("receives Kaspi QR as money already on hand", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: /Kaspi QR/ }));
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "0.01");
    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "0.01",
      method: "kaspi",
      stage: "received",
    });
  });

  it("records a remote payment as money already received, sending nothing to the client", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: /Удалённая оплата/ }));
    expect(screen.queryByLabelText(/Телефон/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledTimes(1);
    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "remote",
      stage: "received",
    });
  });

  it("sends a Kaspi invoice to the client's phone without a PDF option", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Отправить удалённый счёт/ }));
    expect(screen.queryByText(/PDF/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Телефон клиента в Kaspi")).toHaveValue("87001234567");
    await user.click(screen.getByRole("button", { name: "Отправить счёт" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "invoice",
      stage: "requested",
      channel: "remote",
      phone_number: "87001234567",
    });
  });

  it("falls back to the client phone and keeps the error inside the dialog", async () => {
    postMock.mockRejectedValueOnce(new Error("boom"));
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(
      <OrderPaymentActions
        order={{ ...order, client_phone: "" } as Order}
        me={me}
        clientPhone="87770001122"
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Отправить удалённый счёт/ }));
    expect(screen.getByLabelText("Телефон клиента в Kaspi")).toHaveValue("87770001122");
    await user.click(screen.getByRole("button", { name: "Отправить счёт" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Ошибка оплаты");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("does not offer more than the remainder left after pending payments", async () => {
    const user = userEvent.setup();
    const reserved = {
      ...order,
      pending_payments: [{ id: 9, amount: "700000", method: "invoice", status: "requested" }],
    } as unknown as Order;
    render(<OrderPaymentActions order={reserved} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    expect(screen.getByLabelText("Сумма")).toHaveValue(7000);
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "8000");
    expect(screen.getByText("Сумма больше остатка к оплате.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Принять" })).toBeDisabled();
  });

  it("offers only cash for dollar orders", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={{ ...order, currency: "USD" } as Order} me={me} onChanged={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /Отправить удалённый счёт/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    expect(screen.queryByRole("button", { name: /Kaspi QR/ })).not.toBeInTheDocument();
  });

  it("hides for unshipped orders, paid orders and users without payments.create", () => {
    const { container, rerender } = render(
      <OrderPaymentActions order={{ ...order, status: "confirmed" } as Order} me={me} onChanged={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(<OrderPaymentActions order={{ ...order, remaining_amount: "0" } as Order} me={me} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(
      <OrderPaymentActions order={order} me={{ ...me, permissions: ["payments.view"] } as Me} onChanged={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("disables both actions while the store payment window is closed", () => {
    render(
      <OrderPaymentActions order={order} me={me} blockedReason="Окно оплаты магазина закрыто" onChanged={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeDisabled();
    expect(screen.getByText("Окно оплаты магазина закрыто")).toBeInTheDocument();
  });
});

describe("paymentAmountProblem", () => {
  it("accepts tiyn precision within the remainder", () => {
    expect(paymentAmountProblem("0.01", 100)).toBe("");
    expect(paymentAmountProblem("1", 100)).toBe("");
  });

  it("rejects empty, fractional tiyn and excess amounts", () => {
    expect(paymentAmountProblem("", 100)).toMatch(/больше нуля/);
    expect(paymentAmountProblem("0.001", 100)).toMatch(/тиына/);
    expect(paymentAmountProblem("1.01", 100)).toMatch(/больше остатка/);
  });
});
