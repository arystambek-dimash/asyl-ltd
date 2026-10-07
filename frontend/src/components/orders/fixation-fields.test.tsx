import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderFixationModal, canFixateOrder } from "@/components/order-fixation-modal";
import type { Order } from "@/lib/types";
import {
  FixationFields,
  emptyFixationDraft,
  fixationDraftError,
  fixationPaymentAllowed,
  type FixationDraft,
} from "./fixation-fields";

const postMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { post: postMock }, apiError: () => "Ошибка" }));
const authMe = vi.hoisted(() => ({ is_superuser: false, permissions: ["orders.edit", "payments.create"] }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: authMe }) }));

const draft = (patch: Partial<FixationDraft> = {}): FixationDraft => ({
  ...emptyFixationDraft(),
  date: "2026-09-10",
  ...patch,
});

describe("fixation payment", () => {
  it("is allowed for a confirmed or shipped target: a prepayment is money like any other", () => {
    expect(fixationPaymentAllowed(draft({ status: "confirmed" }))).toBe(true);
    expect(fixationPaymentAllowed(draft({ status: "shipped" }))).toBe(true);
    // Новый заказ без статуса — подтверждение ещё не выбрано.
    expect(fixationPaymentAllowed(draft())).toBe(false);
    // Существующий заказ уже подтверждён — оплату можно зафиксировать, не меняя статус.
    expect(fixationPaymentAllowed(draft(), "loading")).toBe(true);
  });

  it("explains a payment without a status and accepts a prepayment of a confirmed order", () => {
    expect(fixationDraftError(draft({ paid: true }), { currency: "KZT" })).toMatch(/подтверждённого или отгруженного/);
    expect(fixationDraftError(draft({ paid: true, status: "confirmed" }), { currency: "KZT" })).toBe("");
    expect(fixationDraftError(draft({ paid: true }), { orderStatus: "confirmed", currency: "KZT" })).toBe("");
  });

  it("takes a dollar order only in cash, as the server does", () => {
    const kaspi = draft({ paid: true, status: "shipped", paymentMethod: "kaspi" });
    expect(fixationDraftError(kaspi, { currency: "USD" })).toMatch(/только в тенге/);
    expect(fixationDraftError(kaspi, { currency: "KZT" })).toBe("");
    expect(fixationDraftError({ ...kaspi, paymentMethod: "cash" }, { currency: "USD" })).toBe("");
  });
});

function Harness({ orderStatus, currency = "KZT" }: { orderStatus?: string; currency?: string }) {
  const [value, setValue] = useState<FixationDraft>(draft());
  return <FixationFields draft={value} onChange={setValue} canPay currency={currency} orderStatus={orderStatus} />;
}

describe("FixationFields", () => {
  it("lets a new confirmed order be marked as paid", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const paid = screen.getByRole("checkbox", { name: /Оплачен полностью/ });
    expect(paid).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Ожидает загрузки/ }));
    expect(paid).toBeEnabled();
    await user.click(paid);
    // Смена статуса не сбрасывает оплату: она открыта для обоих.
    await user.click(screen.getByRole("radio", { name: /Отгружено/ }));
    expect(paid).toBeChecked();
  });

  it("keeps the status of an existing unshipped order when asked", () => {
    render(<Harness orderStatus="loading" />);
    expect(screen.getByRole("radio", { name: /Не менять/ })).toBeChecked();
    // «Ожидает загрузки» здесь — подпись текущего статуса, а не отдельный выбор.
    expect(screen.getAllByRole("radio").map((radio) => radio.textContent)).toEqual([
      "Не менятьОжидает загрузки",
      "ОтгруженоСклад не списывается",
    ]);
    expect(screen.getByRole("checkbox", { name: /Оплачен полностью/ })).toBeEnabled();
  });

  it("offers a dollar order only cash", async () => {
    const user = userEvent.setup();
    render(<Harness orderStatus="shipped" currency="USD" />);
    await user.click(screen.getByRole("checkbox", { name: /Оплачен полностью/ }));
    const methods = screen.getByRole("radiogroup", { name: "Способ оплаты" });
    expect(
      within(methods)
        .getAllByRole("radio")
        .map((radio) => radio.textContent),
    ).toEqual(["Наличные"]);
  });
});

describe("OrderFixationModal", () => {
  beforeEach(() => {
    authMe.is_superuser = false;
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 5 } });
  });

  it("opens a paid shipped order only for the superuser", () => {
    const paidShipped = { status: "shipped", is_fully_paid: true } as Order;
    expect(canFixateOrder(paidShipped)).toBe(false);
    expect(canFixateOrder(paidShipped, { superuser: true })).toBe(false);
    expect(canFixateOrder({ ...paidShipped, shipped_at: "2026-09-01T07:11:00Z" }, { superuser: true })).toBe(true);
    expect(canFixateOrder({ status: "shipped", is_fully_paid: false } as Order)).toBe(true);
  });

  it("lets the superuser move a shipment to another day without touching money", async () => {
    authMe.is_superuser = true;
    const user = userEvent.setup();
    const order = {
      id: 5,
      status: "shipped",
      currency: "KZT",
      is_fully_paid: true,
      shipped_at: "2026-09-01T07:11:00Z",
    } as Order;
    render(<OrderFixationModal order={order} onClose={vi.fn()} onChanged={vi.fn()} />);

    expect(screen.getByText(/уже отгружен и оплачен/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Оплачен полностью/ })).not.toBeInTheDocument();
    const fixate = screen.getByRole("button", { name: /Зафиксировать/ });
    expect(fixate).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Перенести отгрузку/ }));
    await user.click(fixate);

    expect(postMock).toHaveBeenCalledWith(
      "/orders/5/fixate/",
      expect.objectContaining({ status: "shipped", paid: false }),
    );
  });

  it("keeps only the payment, pre-checked, when a shipment cannot be moved", () => {
    authMe.is_superuser = true;
    const order = { id: 5, status: "shipped", currency: "KZT", is_fully_paid: false, shipped_at: null } as Order;
    render(<OrderFixationModal order={order} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.queryByRole("radio", { name: /Перенести отгрузку/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Оплачен полностью/ })).toBeChecked();
  });

  it("does not offer moving a shipment to staff", () => {
    const order = { id: 5, status: "shipped", currency: "KZT", is_fully_paid: false } as Order;
    render(<OrderFixationModal order={order} onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.queryByRole("radio", { name: /Перенести отгрузку/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Оплачен полностью/ })).toBeChecked();
  });

  it("fixes only the payment of a confirmed order, keeping its status", async () => {
    const user = userEvent.setup();
    const order = { id: 5, status: "confirmed", currency: "KZT", is_fully_paid: false } as Order;
    render(<OrderFixationModal order={order} onClose={vi.fn()} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("radio", { name: /Не менять/ }));
    await user.click(screen.getByRole("checkbox", { name: /Оплачен полностью/ }));
    await user.click(screen.getByRole("button", { name: /Зафиксировать/ }));

    expect(postMock).toHaveBeenCalledWith(
      "/orders/5/fixate/",
      expect.objectContaining({ status: null, paid: true, payment_method: "cash" }),
    );
  });
});
