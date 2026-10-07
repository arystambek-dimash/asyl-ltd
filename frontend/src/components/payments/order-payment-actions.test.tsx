import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderPaymentActions } from "./order-payment-actions";
import type { Order } from "@/lib/types";
import { makeMe } from "@/test-utils/factories";

const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Ошибка оплаты",
}));

const me = makeMe({ username: "cashier", permissions: ["payments.create"] });

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
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote", "invoice"],
  payment_request_open: true,
  payments: [],
  pending_payments: [],
} as unknown as Order;

/** Подтверждённый, ещё не отгруженный заказ: сервер открывает только деньги у кассы. */
const prepaidOrder = {
  ...order,
  status: "confirmed",
  payment_open_methods: ["cash", "kaspi", "remote"],
  payment_request_open: false,
} as Order;

beforeEach(() => {
  postMock.mockReset();
  postMock.mockResolvedValue({ data: {} });
});

describe("OrderPaymentActions", () => {
  it("offers only receiving money and a remote invoice", () => {
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeInTheDocument();
  });

  it("asks the method only after «Принять», with nothing preselected, and records cash on «Наличные»", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OrderPaymentActions order={order} me={me} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    const dialog = screen.getByRole("dialog", { name: "Принять оплату" });
    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(707000);
    // На шаге суммы способа нет: кассир не может «забыть переключить» заранее выбранный.
    expect(within(dialog).queryByRole("button", { name: /Наличные/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Принять" }));

    expect(postMock).not.toHaveBeenCalled();
    expect(dialog).toHaveAccessibleName(/^Как клиент заплатил 707\s000 ₸\?$/);
    expect(within(dialog).queryByLabelText("Сумма")).not.toBeInTheDocument();
    for (const name of [/Наличные/, "QR", /Удалённая оплата/]) {
      expect(within(dialog).getByRole("button", { name })).not.toHaveAttribute("aria-pressed");
    }
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    expect(postMock).toHaveBeenCalledTimes(1);
    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "cash",
    });
    expect(onChanged).toHaveBeenCalledWith(expect.stringContaining("долг уменьшен"));
  });

  it("records money received on a past day with that date", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OrderPaymentActions order={order} me={me} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    const dateField = screen.getByLabelText("Дата оплаты");
    fireEvent.change(dateField, { target: { value: "2026-09-15" } });
    expect(screen.getByText(/Оплата запишется 15\.09\.2026/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await user.click(screen.getByRole("button", { name: "QR" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "kaspi",
      date: "2026-09-15",
    });
    expect(onChanged).toHaveBeenCalledWith(expect.stringContaining("датой 15.09.2026"));
  });

  it("does not let a payment date in the future through", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    fireEvent.change(screen.getByLabelText("Дата оплаты"), { target: { value: "2099-01-01" } });

    expect(screen.getByRole("button", { name: "Принять" })).toBeDisabled();
    expect(screen.getByText("Дата оплаты не может быть в будущем.")).toBeInTheDocument();
  });

  it("receives QR at the till terminal as money already on hand", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "0.01");
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await user.click(screen.getByRole("button", { name: "QR" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "0.01",
      method: "kaspi",
    });
  });

  it("confirms the amount by Enter, and a second Enter records nothing", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.type(screen.getByLabelText("Сумма"), "{Enter}");

    // Фокус на списке способов, не на «Наличные»: повторный Enter оплату не записывает.
    expect(screen.getByRole("group", { name: "Способ оплаты" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(postMock).not.toHaveBeenCalled();
  });

  it("goes «Назад» to the amount and keeps it", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "1000");
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await user.click(screen.getByRole("button", { name: "Назад" }));

    const dialog = screen.getByRole("dialog", { name: "Принять оплату" });
    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(1000);
    expect(within(dialog).queryByRole("button", { name: /Наличные/ })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("shows the payment in progress on the tapped method and locks the others", async () => {
    let finish: (value: unknown) => void = () => {};
    postMock.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: "Принять" }));
    await user.click(screen.getByRole("button", { name: "QR" }));

    expect(screen.getByRole("button", { name: "QR" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "QR" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Наличные/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Наличные/ })).not.toHaveAttribute("aria-busy");
    expect(screen.getByRole("button", { name: "Назад" })).toBeDisabled();
    finish({ data: {} });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("records a remote payment as money already received, sending nothing to the client", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: "Принять" }));
    expect(screen.queryByLabelText(/Телефон/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Удалённая оплата/ }));

    expect(postMock).toHaveBeenCalledTimes(1);
    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "remote",
    });
  });

  it("sends a Kaspi invoice to the client's phone without a PDF option", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Отправить удалённый счёт/ }));
    expect(screen.queryByText(/PDF/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Телефон клиента в Kaspi")).toHaveValue("87001234567");
    await user.click(screen.getByRole("button", { name: "Отправить счёт" }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "invoice",
      phone_number: "87001234567",
    });
  });

  it("falls back to the client phone and keeps the error inside the dialog", async () => {
    postMock.mockRejectedValueOnce(new Error("boom"));
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(
      <OrderPaymentActions
        order={{ ...order, client_phone: "" } as Order}
        me={me}
        clientPhone="87770001122"
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Отправить удалённый счёт/ }));
    expect(screen.getByLabelText("Телефон клиента в Kaspi")).toHaveValue("87770001122");
    await user.click(screen.getByRole("button", { name: "Отправить счёт" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Ошибка оплаты");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows the remainder under the amount and restores it with «Весь остаток»", async () => {
    const user = userEvent.setup();
    render(<OrderPaymentActions order={order} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    const dialog = screen.getByRole("dialog", { name: "Принять оплату" });
    expect(within(dialog).getByText(/^Остаток к оплате: 707\s000 ₸$/)).toBeInTheDocument();
    // Сумма уже полная — подставлять нечего.
    expect(within(dialog).queryByRole("button", { name: "Весь остаток" })).not.toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText("Сумма"));
    await user.type(within(dialog).getByLabelText("Сумма"), "1000");
    await user.click(within(dialog).getByRole("button", { name: "Весь остаток" }));

    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(707000);
    expect(within(dialog).queryByRole("button", { name: "Весь остаток" })).not.toBeInTheDocument();
  });

  it("keeps a refused payment inside the dialog on the method step", async () => {
    postMock.mockRejectedValueOnce(new Error("boom"));
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OrderPaymentActions order={order} me={me} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: "Принять" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Ошибка оплаты");
    // Окно остаётся на шаге способа: можно нажать способ ещё раз или вернуться к сумме.
    expect(dialog).toHaveAccessibleName(/^Как клиент заплатил 707\s000 ₸\?$/);
    expect(within(dialog).getByRole("button", { name: /Наличные/ })).toBeEnabled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("does not offer more than the remainder left after pending payments", async () => {
    const user = userEvent.setup();
    const reserved = {
      ...order,
      pending_payments: [{ id: 9, amount: "700000", method: "invoice", status: "requested" }],
    } as unknown as Order;
    render(<OrderPaymentActions order={reserved} me={me} onChanged={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    expect(screen.getByLabelText("Сумма")).toHaveValue(7000);
    await user.clear(screen.getByLabelText("Сумма"));
    await user.type(screen.getByLabelText("Сумма"), "8000");
    expect(screen.getByText(/^Доступно не более 7\s000\.$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Принять" })).toBeDisabled();
  });

  it("offers only cash for dollar orders", async () => {
    const user = userEvent.setup();
    // Долларовому заказу сервер открывает только наличные и не открывает счёт на телефон.
    const dollarOrder = {
      ...order,
      currency: "USD",
      payment_open_methods: ["cash"],
      payment_request_open: false,
    } as Order;
    render(<OrderPaymentActions order={dollarOrder} me={me} onChanged={vi.fn()} />);

    expect(screen.queryByRole("button", { name: /Отправить удалённый счёт/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    expect(screen.queryByRole("button", { name: /Наличные/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Принять" }));
    expect(screen.queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалённая оплата/ })).not.toBeInTheDocument();
    // Единственный способ тоже нажимают сами: оплата не записывается без выбора.
    expect(postMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Наличные/ }));
    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", { amount: "707000", method: "cash" });
  });

  it("takes a prepayment before shipment only as money already at the till", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OrderPaymentActions order={prepaidOrder} me={me} onChanged={onChanged} />);

    // Kaspi QR через ApiPay и счёт на телефон ждут отгрузки — сервер их не открывает.
    expect(screen.queryByRole("button", { name: /Отправить удалённый счёт/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    const dialog = screen.getByRole("dialog", { name: "Принять оплату" });
    expect(dialog).toHaveTextContent("предоплата");
    expect(dialog).not.toHaveTextContent("долг");
    await user.click(screen.getByRole("button", { name: "Принять" }));
    expect(screen.getByRole("button", { name: "QR" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Удалённая оплата/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Наличные/ }));

    expect(postMock).toHaveBeenCalledWith("/orders/156/payments/", {
      amount: "707000",
      method: "cash",
    });
    expect(onChanged).toHaveBeenCalledWith(expect.stringContaining("Предоплата"));
    expect(onChanged).not.toHaveBeenCalledWith(expect.stringContaining("долг"));
  });

  it("offers only the methods the server opened", async () => {
    const user = userEvent.setup();
    render(
      <OrderPaymentActions
        order={{ ...prepaidOrder, payment_open_methods: ["cash", "remote"] } as Order}
        me={me}
        onChanged={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
    await user.click(screen.getByRole("button", { name: "Принять" }));
    expect(screen.queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Наличные/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Удалённая оплата/ })).toBeInTheDocument();
  });

  it("opens the receive dialog prefilled when the order form could not take the payment", async () => {
    const user = userEvent.setup();
    const onAutoOpened = vi.fn();
    render(
      <OrderPaymentActions
        order={prepaidOrder}
        me={me}
        onChanged={vi.fn()}
        autoOpen={{ amount: "500", notice: "Оплата не прошла — примите её ещё раз." }}
        onAutoOpened={onAutoOpened}
      />,
    );

    const dialog = await screen.findByRole("dialog", { name: "Принять оплату" });
    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(500);
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Оплата не прошла");
    expect(onAutoOpened).toHaveBeenCalledOnce();
    // Способ из формы заказа не подставляется: его снова выбирают после «Принять».
    expect(within(dialog).queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Принять" }));
    expect(within(dialog).getByRole("button", { name: "QR" })).not.toHaveAttribute("aria-pressed");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hides when the server closes payment, for paid orders and users without payments.create", () => {
    const { container, rerender } = render(
      <OrderPaymentActions
        order={
          {
            ...order,
            status: "pending",
            payment_open: false,
            payment_open_methods: [],
            payment_request_open: false,
          } as Order
        }
        me={me}
        onChanged={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(<OrderPaymentActions order={{ ...order, remaining_amount: "0" } as Order} me={me} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<OrderPaymentActions order={order} me={{ ...me, permissions: ["payments.view"] }} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("disables both actions while the store payment window is closed", () => {
    render(
      <OrderPaymentActions order={order} me={me} blockedReason="Окно оплаты магазина закрыто" onChanged={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeDisabled();
    expect(screen.getByText("Окно оплаты магазина закрыто")).toBeInTheDocument();
  });
});
