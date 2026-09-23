import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderFixationModal } from "@/components/order-fixation-modal";
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
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { is_superuser: false, permissions: ["orders.edit", "payments.create"] } }),
}));

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
    expect(fixationDraftError(draft({ paid: true }))).toMatch(/подтверждённого или отгруженного/);
    expect(fixationDraftError(draft({ paid: true, status: "confirmed" }))).toBe("");
    expect(fixationDraftError(draft({ paid: true }), { orderStatus: "confirmed" })).toBe("");
  });
});

function Harness({ orderStatus }: { orderStatus?: string }) {
  const [value, setValue] = useState<FixationDraft>(draft());
  return <FixationFields draft={value} onChange={setValue} canPay orderStatus={orderStatus} />;
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
});

describe("OrderFixationModal", () => {
  beforeEach(() => {
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 5 } });
  });

  it("fixes only the payment of a confirmed order, keeping its status", async () => {
    const user = userEvent.setup();
    const order = { id: 5, status: "confirmed", is_fully_paid: false } as Order;
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
