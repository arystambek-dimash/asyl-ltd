import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { makeQrRefund } from "@/test-utils/factories";
import { QrRefundModal } from "./qr-refund-modal";

const mocks = vi.hoisted(() => ({ post: vi.fn(), useApi: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: vi.fn() }));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const payment = { id: 160, order: 590, amount: "5000", currency: "KZT", client_name: "Нуржан" } as Payment;

beforeEach(() => {
  mocks.post.mockReset();
  mocks.useApi.mockReset().mockReturnValue({ data: null, error: "", reload: vi.fn() });
});

it("shows the buyer a QR on the cashier screen and the same link to send while waiting", () => {
  render(<QrRefundModal payment={payment} initial={makeQrRefund({})} onClose={vi.fn()} onChanged={vi.fn()} />);

  expect(screen.getByRole("dialog", { name: "Возврат по QR" })).toBeInTheDocument();
  expect(screen.getByTitle("QR для возврата оплаты")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /WhatsApp/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Скопировать/ })).toBeInTheDocument();
  expect(screen.getByText(/Покажите QR покупателю или отправьте ему ссылку/)).toBeInTheDocument();
});

it("lets the cashier pick the purchase when Kaspi lists several", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn().mockResolvedValue(undefined);
  mocks.post.mockResolvedValue({
    data: makeQrRefund({ status: "completed", active: false, customer_url: null, refunded_amount: "5000.00" }),
  });
  render(
    <QrRefundModal
      payment={payment}
      initial={makeQrRefund({
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
      initial={makeQrRefund({
        status: "execution_uncertain",
        active: false,
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
