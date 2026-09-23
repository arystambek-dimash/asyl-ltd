import { beforeEach, describe, expect, it } from "vitest";
import { emptyFixationDraft } from "@/components/orders/fixation-fields";
import { loadOrderDraft, orderDraftHasContent, saveOrderDraft, type OrderDraft } from "@/lib/order-draft";

const draft = (fields: Partial<OrderDraft> = {}): OrderDraft => ({
  template: null,
  dept: "sales",
  client: "1",
  currency: "KZT",
  store: "",
  warehouse: "",
  transport: "truck",
  truck: "",
  trailer: "",
  wagonNumber: "",
  arrival: "",
  rows: [{ id: 0, product: "2", quantity: "4", price: "10" }],
  backdateOn: false,
  fixation: emptyFixationDraft(),
  ...fields,
});

describe("черновик нового заказа", () => {
  beforeEach(() => localStorage.clear());

  it("«Оплата сразу» после восстановления выключена, способ и сумма остаются", () => {
    saveOrderDraft(5, draft({ payNow: { on: true, method: "kaspi", amount: "25" } }));

    expect(loadOrderDraft(5)?.payNow).toEqual({ on: false, method: "kaspi", amount: "25" });
  });

  it("черновик без «Оплаты сразу» восстанавливается как был", () => {
    saveOrderDraft(5, draft());

    expect(loadOrderDraft(5)).toEqual(draft());
  });

  it("одна включённая «Оплата сразу» — не повод хранить черновик", () => {
    const empty = draft({ client: "", rows: [{ id: 0, product: "", quantity: "", price: "" }] });

    expect(orderDraftHasContent({ ...empty, payNow: { on: true, method: "cash", amount: null } })).toBe(false);
  });
});
