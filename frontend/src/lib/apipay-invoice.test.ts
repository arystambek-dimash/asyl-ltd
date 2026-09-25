import { expect, it } from "vitest";
import { invoiceIsActive, invoiceIsClosed, invoiceIsPayable, invoiceStatusLabel } from "./apipay-invoice";

it("splits ApiPay invoice statuses into payable, active and closed without money", () => {
  expect(["creating", "processing", "pending"].every(invoiceIsPayable)).toBe(true);
  expect(invoiceIsPayable("cancelling")).toBe(false);
  expect(invoiceIsActive("cancelling")).toBe(true);
  expect(["expired", "cancelled", "error", "superseded"].every(invoiceIsClosed)).toBe(true);
  expect(["paid", "partially_refunded"].some((s) => invoiceIsActive(s) || invoiceIsClosed(s))).toBe(false);
});

it("labels invoice statuses in Russian and keeps unknown codes as is", () => {
  expect(invoiceStatusLabel("superseded")).toBe("Заменён");
  expect(invoiceStatusLabel("new_status")).toBe("new_status");
});
