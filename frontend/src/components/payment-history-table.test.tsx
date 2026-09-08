import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { PaymentHistoryTable, type HistoryPayment } from "./payment-history-table";
const mocks = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { post: (...args: unknown[]) => mocks.post(...args) },
  apiError: () => "Платёж уже изменён",
}));
const payment: HistoryPayment = {
  id: 1,
  order_id: 12,
  date: "2026-09-08T10:00:00Z",
  employee: "Кассир",
  method: "cash",
  status: "confirmed",
  amount: "500",
  currency: "KZT",
  can_reopen: true,
};
beforeEach(() => {
  mocks.post.mockReset();
});

it("cancels confirmation only after showing the amount and debt consequence", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  mocks.post.mockResolvedValue({ status: 200 });
  render(
    <PaymentHistoryTable rows={[payment]} emptyText="Пусто" canViewOrders canManagePayments onChanged={changed} />,
  );
  await user.click(screen.getByRole("button", { name: "Отменить подтверждение" }));
  expect(mocks.post).not.toHaveBeenCalled();
  expect(screen.getByText(/долг увеличится/)).toHaveTextContent("500");
  await user.click(screen.getAllByRole("button", { name: "Отменить подтверждение" }).at(-1)!);
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/12/payments/1/reopen/"));
  expect(changed).toHaveBeenCalledOnce();
});

it("keeps the dialog and balances unchanged on a stale payment error", async () => {
  const user = userEvent.setup();
  const changed = vi.fn();
  mocks.post.mockRejectedValue(new Error("stale"));
  render(
    <PaymentHistoryTable rows={[payment]} emptyText="Пусто" canViewOrders canManagePayments onChanged={changed} />,
  );
  await user.click(screen.getByRole("button", { name: "Отменить подтверждение" }));
  await user.click(screen.getAllByRole("button", { name: "Отменить подтверждение" }).at(-1)!);
  expect(await screen.findByRole("alert")).toHaveTextContent("Платёж уже изменён");
  expect(changed).not.toHaveBeenCalled();
});

it("does not offer cancellation to a viewer or for refunded and online payments", () => {
  render(
    <PaymentHistoryTable
      rows={[{ ...payment, can_reopen: false, provider: true }]}
      emptyText="Пусто"
      canViewOrders
      canManagePayments
      onChanged={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /Отменить/ })).not.toBeInTheDocument();
});

it("hides cancellation for a report-only viewer", () => {
  render(
    <PaymentHistoryTable
      rows={[payment]}
      emptyText="Пусто"
      canViewOrders
      canManagePayments={false}
      onChanged={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /Отменить/ })).not.toBeInTheDocument();
});
