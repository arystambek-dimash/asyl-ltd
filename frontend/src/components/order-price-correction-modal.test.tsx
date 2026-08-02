import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderPriceCorrectionModal } from "@/components/order-price-correction-modal";
import type { Order } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка корректировки",
}));

const order = {
  id: 17,
  client: 1,
  currency: "KZT",
  status: "shipped",
  truck_number: "",
  items: [
    { id: 101, product: 1, product_label: "Мука 50 кг", quantity: 20, unit_price: "10.00" },
    { id: 102, product: 2, product_label: "Мука 25 кг", quantity: 30, unit_price: "20.00" },
  ],
  total_amount: "800.00",
  paid_total: "0.00",
  is_fully_paid: false,
  debt_override: false,
  created_at: "2026-08-02T00:00:00Z",
} as Order;

describe("OrderPriceCorrectionModal", () => {
  beforeEach(() => postMock.mockReset());

  it("submits a total that the server will divide by all bags", async () => {
    postMock.mockResolvedValue({ data: {} });
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<OrderPriceCorrectionModal order={order} onClose={vi.fn()} onDone={onDone} />);

    const total = screen.getByLabelText("Новая общая сумма заказа");
    await user.clear(total);
    await user.type(total, "4000000000");

    expect(screen.getByText(/80 000 000.*за мешок/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Сохранить корректировку" }));

    expect(postMock).toHaveBeenCalledWith("/orders/17/correct-price/", {
      total_amount: "4000000000",
    });
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("supports a separate bag price for every order item", async () => {
    postMock.mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    render(<OrderPriceCorrectionModal order={order} onClose={vi.fn()} onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "По позициям" }));
    const first = screen.getByLabelText("Новая цена за мешок, Мука 50 кг");
    const second = screen.getByLabelText("Новая цена за мешок, Мука 25 кг");
    await user.clear(first);
    await user.type(first, "30");
    await user.clear(second);
    await user.type(second, "40");
    await user.click(screen.getByRole("button", { name: "Сохранить корректировку" }));

    expect(postMock).toHaveBeenCalledWith("/orders/17/correct-price/", {
      prices: { "101": "30", "102": "40" },
    });
  });
});
