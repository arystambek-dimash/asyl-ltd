import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AddPaymentActions } from "./payment-chain";
import type { Me, Order } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка оплаты",
}));

const me = {
  id: 1,
  username: "cashier",
  // Кассир: вносит и подтверждает — его оплата закрывается сразу.
  permissions: ["payments.create", "payments.confirm"],
  is_superuser: false,
} as unknown as Me;

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
  settlement_intent: "debt",
  payments: [],
} as unknown as Order;

async function openReceiveModal() {
  const user = userEvent.setup();
  render(<AddPaymentActions order={order} me={me} onChanged={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
  return user;
}

describe("AddPaymentActions — счёт на оплату", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: {} });
  });

  it("hides the invoice channel picker for other methods", async () => {
    await openReceiveModal();

    // Наличные — канала нет, модалка остаётся простой.
    expect(screen.queryByText("Наш PDF-счёт")).not.toBeInTheDocument();
    expect(screen.queryByText("Счёт клиенту")).not.toBeInTheDocument();
  });

  it("offers Kaspi and our PDF once the invoice method is chosen", async () => {
    const user = await openReceiveModal();

    await user.selectOptions(screen.getByLabelText("Способ"), "invoice");

    expect(screen.getByText("Счёт клиенту")).toBeInTheDocument();
    expect(screen.getByText("Наш PDF-счёт")).toBeInTheDocument();
    // По умолчанию — онлайн-счёт с телефоном клиента.
    expect(screen.getByLabelText("Телефон для счёта на оплату")).toHaveValue("87001234567");
  });

  it("sends channel=document and drops the phone for our PDF invoice", async () => {
    const user = await openReceiveModal();

    await user.selectOptions(screen.getByLabelText("Способ"), "invoice");
    await user.click(screen.getByText("Наш PDF-счёт"));

    // Телефон для документа не нужен — вместо него подсказка про портал.
    expect(screen.queryByLabelText("Телефон для счёта на оплату")).not.toBeInTheDocument();
    expect(screen.getByText(/уведомление в портале/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "invoice",
      stage: "received",
      channel: "document",
    });
  });

  it("sends channel=remote with the phone for an online invoice", async () => {
    const user = await openReceiveModal();

    await user.selectOptions(screen.getByLabelText("Способ"), "invoice");
    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "invoice",
      stage: "received",
      channel: "remote",
      phone_number: "87001234567",
    });
  });

  it("tells a cashier the payment lands immediately", async () => {
    await openReceiveModal();

    expect(screen.getByText(/сразу уменьшит долг/)).toBeInTheDocument();
  });

  it("tells a manager without the perm that the cashier confirms", async () => {
    const user = userEvent.setup();
    const recorder = { ...me, permissions: ["payments.create"] } as unknown as Me;
    render(<AddPaymentActions order={order} me={recorder} onChanged={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));

    expect(screen.getByText(/после ручного подтверждения кассиром/)).toBeInTheDocument();
  });

  it("omits channel entirely for cash", async () => {
    const user = await openReceiveModal();

    await user.click(screen.getByRole("button", { name: "Принять" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "cash",
      stage: "received",
    });
  });
});
