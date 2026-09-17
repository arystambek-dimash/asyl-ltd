import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Payment, QrRefundState } from "@/lib/types";
import { QrRefundModal } from "./qr-refund-modal";

const mocks = vi.hoisted(() => ({ post: vi.fn(), useApi: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: vi.fn() }));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const payment = { id: 160, order: 590, amount: "5000", currency: "KZT", client_name: "Нуржан" } as Payment;

const state = (fields: Partial<QrRefundState>): QrRefundState => ({
  id: 1,
  status: "awaiting_customer",
  amount: "5000.00",
  refunded_amount: null,
  client_name: null,
  customer_url: "https://qr.apipay.kz/refund/token",
  link_expires_at: "2026-09-18T10:00:00+05:00",
  operations: [],
  receipt_url: null,
  error_code: null,
  error_message: null,
  created_at: "2026-09-17T10:00:00+05:00",
  ...fields,
});

beforeEach(() => {
  mocks.post.mockReset();
  mocks.useApi.mockReset().mockReturnValue({ data: null, error: "", reload: vi.fn() });
});

it("shows the buyer link with send actions while waiting for the buyer", () => {
  render(<QrRefundModal payment={payment} initial={state({})} onClose={vi.fn()} onChanged={vi.fn()} />);

  expect(screen.getByText("https://qr.apipay.kz/refund/token")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /WhatsApp/ })).toBeInTheDocument();
  expect(screen.getByText(/Отправьте ссылку покупателю/)).toBeInTheDocument();
});

it("lets the cashier pick the purchase when Kaspi lists several", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn().mockResolvedValue(undefined);
  mocks.post.mockResolvedValue({
    data: state({ status: "completed", customer_url: null, refunded_amount: "5000.00" }),
  });
  render(
    <QrRefundModal
      payment={payment}
      initial={state({
        status: "customer_identified",
        customer_url: null,
        operations: [
          { ref: "op-a", amount: "5000.00", date: null, returnable: "full", client_name: null },
          { ref: "op-b", amount: "5000.00", date: null, returnable: "full", client_name: null },
        ],
      })}
      onClose={vi.fn()}
      onChanged={onChanged}
    />,
  );

  await user.click(screen.getAllByRole("button", { name: "Вернуть" })[1]);

  expect(mocks.post).toHaveBeenCalledWith("/payment-transactions/160/qr-refund/execute/", { operation_ref: "op-b" });
  await waitFor(() => expect(screen.getByText(/Деньги возвращены/)).toBeInTheDocument());
  expect(onChanged).toHaveBeenCalled();
});

it("warns not to repeat when Kaspi did not prove the outcome", () => {
  render(
    <QrRefundModal
      payment={payment}
      initial={state({
        status: "execution_uncertain",
        customer_url: null,
        error_message: "Kaspi не подтвердил исход возврата — деньги могли уйти. Не повторяйте возврат.",
      })}
      onClose={vi.fn()}
      onChanged={vi.fn()}
    />,
  );

  expect(screen.getByText(/Не повторяйте возврат/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /WhatsApp/ })).not.toBeInTheDocument();
});
