import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Order } from "@/lib/types";
import { payAfterCreate, payRetryFromParams, payRetryHref, payRetryPageNotice, withoutPayRetry } from "./pay-now";

const postMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { post: postMock }, apiError: () => "Ошибка" }));

const confirmed = {
  id: 12,
  status: "confirmed",
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote"],
} as unknown as Order;

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ data: {} });
});

describe("payAfterCreate", () => {
  it("takes the prepayment of a confirmed order and opens it", async () => {
    await expect(payAfterCreate(confirmed, { method: "kaspi", amount: "500" })).resolves.toBe("/orders/12");
    // То же тело, что у «Принять оплату».
    expect(postMock).toHaveBeenCalledWith("/orders/12/payments/", {
      amount: "500",
      method: "kaspi",
      stage: "received",
    });
  });

  it("sends the cashier to the order with the receive dialog when the server rejects the payment", async () => {
    postMock.mockRejectedValueOnce({ response: { status: 400, data: { detail: "Сумма больше остатка" } } });
    await expect(payAfterCreate(confirmed, { method: "cash", amount: "500" })).resolves.toBe(
      "/orders/12?pay=cash&amount=500",
    );
  });

  it.each([
    ["no answer", new Error("offline")],
    ["a gateway error", { response: { status: 502, data: "" } }],
  ])("asks to check the order instead of taking the money again after %s", async (_case, failure) => {
    // Оплата могла записаться: повторный приём той же суммы задвоил бы деньги.
    postMock.mockRejectedValueOnce(failure);
    await expect(payAfterCreate(confirmed, { method: "cash", amount: "500" })).resolves.toBe(
      "/orders/12?pay=cash&amount=500&pay_check=1",
    );
  });

  it("does not try to take money for an order that was not confirmed", async () => {
    const pending = { ...confirmed, status: "pending", payment_open: false, payment_open_methods: [] } as Order;
    await expect(payAfterCreate(pending, { method: "cash", amount: "500" })).resolves.toBe(
      "/orders/12?pay=cash&amount=500",
    );
    expect(postMock).not.toHaveBeenCalled();
  });
});

describe("pay retry link", () => {
  it("round-trips the method and amount through the order address", () => {
    const href = payRetryHref(12, { method: "remote", amount: "1500.5" });
    const retry = payRetryFromParams(new URLSearchParams(href.split("?")[1]));
    expect(retry).toMatchObject({ method: "remote", amount: "1500.5", check: false });
    expect(retry?.notice).toMatch(/оплата не прошла/i);
    expect(payRetryFromParams(new URLSearchParams("back=%2Forders"))).toBeNull();

    const lost = payRetryHref(12, { method: "cash", amount: "500" }, { check: true });
    expect(payRetryFromParams(new URLSearchParams(lost.split("?")[1]))).toMatchObject({ check: true });
    expect(withoutPayRetry(new URLSearchParams(lost.split("?")[1]))).toBe("");
  });
});

describe("payRetryPageNotice", () => {
  const retry = { method: "cash", amount: "500", notice: "", check: false };

  it("opens the receive dialog only when the payment surely was not recorded", () => {
    expect(payRetryPageNotice(retry, { ...confirmed, currency: "KZT", paid_total: "0" } as Order)).toBe("");
  });

  it("explains an unconfirmed order", () => {
    const pending = { ...confirmed, payment_open: false, payment_open_methods: [] } as Order;
    expect(payRetryPageNotice(retry, pending)).toMatch(/ещё не подтверждён/);
  });

  it("shows what is paid when the payment answer was lost", () => {
    const order = { ...confirmed, currency: "KZT", paid_total: "500" } as Order;
    const notice = payRetryPageNotice({ ...retry, check: true }, order);
    expect(notice).toMatch(/ответ об оплате не пришёл/);
    expect(notice).toContain("500 ₸");
  });
});
