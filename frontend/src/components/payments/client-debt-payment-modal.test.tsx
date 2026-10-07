import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClientDebtPaymentModal } from "./client-debt-payment-modal";
import type { DebtPaymentPlan, DebtPaymentSlice } from "@/lib/debt-orders";

const postMock = vi.hoisted(() => vi.fn());

// apiError и apiErrorCode настоящие: текст и код отказа приходят в теле ответа сервера.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { post: postMock },
}));
// Без задержки: предпросмотр уходит на каждый ввод суммы.
vi.mock("@/lib/use-debounced", () => ({ useDebounced: (value: string) => value }));

/** Предпросмотр уходит без способа: его выбирают только после «Подтвердить». */
type Body = { amount: string | null; method?: string; currency: string; preview: boolean; date?: string };

const ENDPOINT = "/clients/7/debt-payment/";

const CLOSED_840: DebtPaymentSlice = {
  order_id: 840,
  shipped_at: "2026-09-08T12:00:00+05:00",
  remaining_before: "1900000.00",
  amount: "1900000.00",
  remaining_after: "0.00",
  closes: true,
  payment_id: null,
};
const PARTIAL_843: DebtPaymentSlice = {
  order_id: 843,
  shipped_at: null,
  remaining_before: "1209500.00",
  amount: "300000.00",
  remaining_after: "909500.00",
  closes: false,
  payment_id: null,
};
const CLOSED_843: DebtPaymentSlice = { ...PARTIAL_843, amount: "1209500.00", remaining_after: "0.00", closes: true };

function plan(body: Body, amount: string, slices: DebtPaymentSlice[]): DebtPaymentPlan {
  return {
    currency: body.currency,
    method: body.method ?? null,
    amount,
    total_available: "3109500.00",
    slices: body.preview ? slices : slices.map((slice, index) => ({ ...slice, payment_id: 500 + index })),
    skipped: [{ order_id: 901, reason: "payment_window_closed", detail: "не день оплаты по графику магазина" }],
  };
}

function apiFailure(code: string, detail: string, extra: Record<string, string> = {}) {
  return Object.assign(new Error(detail), { response: { status: 400, data: { detail, code, ...extra } } });
}

/** Сервер: весь долг 3 109 500 ₸ на #840 и #843, #901 пропущен; больше долга — amount_exceeds_debt. */
function serve(body: Body): DebtPaymentPlan {
  if (body.amount === null) return plan(body, "3109500.00", [CLOSED_840, CLOSED_843]);
  if (Number(body.amount) > 3109500) {
    throw apiFailure("amount_exceeds_debt", "Максимум к оплате 3 109 500 ₸", { max_amount: "3109500.00" });
  }
  if (body.amount === "2200000") return plan(body, "2200000.00", [CLOSED_840, PARTIAL_843]);
  if (body.amount === "3109500") return plan(body, "3109500.00", [CLOSED_840, CLOSED_843]);
  if (body.amount === "500") return plan(body, "500.00", [{ ...PARTIAL_843, amount: "500.00", closes: false }]);
  return plan(body, body.amount, []);
}

function renderModal(props: Partial<Parameters<typeof ClientDebtPaymentModal>[0]> = {}) {
  const onPaid = vi.fn();
  const onClose = vi.fn();
  render(
    <ClientDebtPaymentModal clientId={7} currencies={["KZT"]} open onClose={onClose} onPaid={onPaid} {...props} />,
  );
  return { onPaid, onClose, dialog: screen.getByRole("dialog", { name: "Внести оплату" }) };
}

beforeEach(() => {
  postMock.mockReset();
  postMock.mockImplementation(async (_url: string, body: Body) => ({ data: serve(body) }));
});

describe("ClientDebtPaymentModal", () => {
  it("previews and records a payment received on a past day with that date", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid } = renderModal();

    fireEvent.change(within(dialog).getByLabelText("Дата оплаты"), { target: { value: "2026-09-20" } });
    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await waitFor(() =>
      expect(postMock).toHaveBeenLastCalledWith(
        ENDPOINT,
        { amount: "2200000", currency: "KZT", preview: true, date: "2026-09-20" },
        expect.anything(),
      ),
    );
    await user.click(await within(dialog).findByRole("button", { name: /Подтвердить · 2\s200\s000 ₸/ }));
    await user.click(screen.getByRole("button", { name: /Наличные/ }));

    expect(postMock).toHaveBeenLastCalledWith(ENDPOINT, {
      amount: "2200000",
      method: "cash",
      currency: "KZT",
      preview: false,
      date: "2026-09-20",
    });
    await waitFor(() => expect(onPaid).toHaveBeenCalledWith(expect.stringContaining("датой 20.09.2026")));
  });

  it("renders nothing and asks nothing while closed", () => {
    render(
      <ClientDebtPaymentModal clientId={7} currencies={["KZT"]} open={false} onClose={vi.fn()} onPaid={vi.fn()} />,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("previews the whole debt on open and shows skipped orders with the reason", async () => {
    const { dialog } = renderModal();

    expect(postMock).toHaveBeenCalledWith(
      ENDPOINT,
      { amount: null, currency: "KZT", preview: true },
      expect.anything(),
    );
    expect(await within(dialog).findByText(/^Можно внести: 3\s109\s500 ₸$/)).toBeInTheDocument();
    // Способа на шаге суммы нет: его спрашивают после «Подтвердить».
    expect(within(dialog).queryByRole("button", { name: /Наличные/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Пропущены" })).toHaveTextContent(
      "#901 — не день оплаты по графику магазина",
    );
    // Сумма не введена — разбивки нет, подтверждать нечего.
    expect(within(dialog).queryByRole("region", { name: "Распределение" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
    // Одна валюта — переключателя нет.
    expect(within(dialog).queryByRole("radiogroup", { name: "Валюта" })).not.toBeInTheDocument();
  });

  it("shows the server breakdown from the oldest order and asks the method only after «Подтвердить»", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid, onClose } = renderModal();

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");

    const region = await within(dialog).findByRole("region", { name: "Распределение" });
    const rows = within(region).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("#840 · отгружен 08.09 — 1 900 000 ₸ → закрыт");
    expect(rows[1]).toHaveTextContent("#843 — 300 000 из 1 209 500 ₸ → останется 909 500 ₸");

    await user.click(within(dialog).getByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));

    // «Подтвердить» ещё ничего не записывает: окно спрашивает, как клиент заплатил.
    expect(postMock.mock.calls.every(([, body]) => (body as Body).preview)).toBe(true);
    expect(dialog).toHaveAccessibleName(/^Как клиент заплатил 2\s200\s000 ₸\?$/);
    expect(within(dialog).queryByLabelText("Сумма")).not.toBeInTheDocument();
    for (const name of [/Наличные/, "QR", /Удалённая оплата/]) {
      expect(within(dialog).getByRole("button", { name })).not.toHaveAttribute("aria-pressed");
    }
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    expect(postMock).toHaveBeenLastCalledWith(ENDPOINT, {
      amount: "2200000",
      method: "cash",
      currency: "KZT",
      preview: false,
    });
    expect(onPaid).toHaveBeenCalledWith(expect.stringMatching(/^Внесено 2\s200\s000 ₸ на 2 заказа$/));
    expect(onClose).toHaveBeenCalled();
  });

  it("fills the whole available debt from the preview", async () => {
    const user = userEvent.setup();
    const { dialog } = renderModal();

    await user.click(await within(dialog).findByRole("button", { name: "Весь долг" }));

    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(3109500);
    expect(postMock).toHaveBeenLastCalledWith(
      ENDPOINT,
      { amount: "3109500", currency: "KZT", preview: true },
      expect.anything(),
    );
    expect(await within(dialog).findByRole("button", { name: /^Подтвердить · 3\s109\s500 ₸$/ })).toBeEnabled();
    expect(within(dialog).queryByRole("button", { name: "Весь долг" })).not.toBeInTheDocument();
  });

  it("shows the server maximum and disables confirm when the amount exceeds the debt", async () => {
    const user = userEvent.setup();
    const { dialog } = renderModal();

    await user.type(within(dialog).getByLabelText("Сумма"), "4000000");

    expect(await within(dialog).findByText("Максимум к оплате 3 109 500 ₸")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "Распределение" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
  });

  it("keeps the skipped orders next to the server maximum", async () => {
    const user = userEvent.setup();
    const { dialog } = renderModal();
    await within(dialog).findByRole("region", { name: "Пропущены" });

    await user.type(within(dialog).getByLabelText("Сумма"), "4000000");

    // Кассир видит, почему максимум меньше долга на карточке: #901 сейчас не оплатить.
    expect(await within(dialog).findByText("Максимум к оплате 3 109 500 ₸")).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Пропущены" })).toHaveTextContent(
      "#901 — не день оплаты по графику магазина",
    );
  });

  it("re-reads the debt after a refused payment and keeps the reason in the dialog", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid, onClose } = renderModal();
    let paidElsewhere = false;
    postMock.mockImplementation(async (_url: string, body: Body) => {
      // Пока окно открыто, часть долга погасили в другом месте: осталось 2 000 000 ₸.
      if (!body.preview || (paidElsewhere && Number(body.amount) > 2000000)) {
        paidElsewhere = true;
        throw apiFailure("amount_exceeds_debt", "Максимум к оплате 2 000 000 ₸", { max_amount: "2000000.00" });
      }
      return { data: serve(body) };
    });
    expect(await within(dialog).findByText(/^Можно внести: 3\s109\s500 ₸$/)).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    // Отказ по сумме возвращает к шагу суммы: причина под полем, предпросмотр пересчитан.
    expect(await within(dialog).findByText(/^Можно внести: 2\s000\s000 ₸$/)).toBeInTheDocument();
    expect(dialog).toHaveAccessibleName("Внести оплату");
    expect(postMock).toHaveBeenLastCalledWith(
      ENDPOINT,
      { amount: "2200000", currency: "KZT", preview: true },
      expect.anything(),
    );
    expect(within(dialog).getByText("Максимум к оплате 2 000 000 ₸")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "Распределение" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
    expect(onPaid).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Весь долг" }));
    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(2000000);
  });

  it("offers only cash in dollars, still as a tap after «Подтвердить»", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid } = renderModal({ currencies: ["KZT", "USD"] });

    await user.click(within(dialog).getByRole("radio", { name: /USD/ }));
    expect(postMock).toHaveBeenLastCalledWith(
      ENDPOINT,
      { amount: null, currency: "USD", preview: true },
      expect.anything(),
    );

    await user.type(within(dialog).getByLabelText("Сумма"), "500");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 500 \$$/ }));

    expect(dialog).toHaveAccessibleName(/^Как клиент заплатил 500 \$\?$/);
    expect(within(dialog).queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Удалённая оплата/ })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith(ENDPOINT, expect.objectContaining({ preview: false }));
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    expect(postMock).toHaveBeenLastCalledWith(ENDPOINT, {
      amount: "500",
      method: "cash",
      currency: "USD",
      preview: false,
    });
    expect(onPaid).toHaveBeenCalledWith("Внесено 500 $ на 1 заказ");
  });

  it.each([
    ["QR", "kaspi"],
    [/Удалённая оплата/, "remote"],
  ])("records the tapped tenge method %s as %s", async (name, method) => {
    const user = userEvent.setup();
    const { dialog } = renderModal();

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));
    await user.click(within(dialog).getByRole("button", { name }));

    expect(postMock).toHaveBeenLastCalledWith(ENDPOINT, {
      amount: "2200000",
      method,
      currency: "KZT",
      preview: false,
    });
  });

  it("puts focus on the method list, so a stray Enter after «Подтвердить» records nothing", async () => {
    const user = userEvent.setup();
    const { dialog } = renderModal();

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));

    // Не на «Наличные»: иначе лишний Enter снова записал бы наличные.
    expect(within(dialog).getByRole("group", { name: "Способ оплаты" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(postMock).not.toHaveBeenCalledWith(ENDPOINT, expect.objectContaining({ preview: false }));
  });

  it("goes «Назад» to the amount and keeps it", async () => {
    const user = userEvent.setup();
    const { dialog } = renderModal();

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));
    await user.click(within(dialog).getByRole("button", { name: "Назад" }));

    expect(dialog).toHaveAccessibleName("Внести оплату");
    expect(within(dialog).getByLabelText("Сумма")).toHaveValue(2200000);
    expect(within(dialog).getByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ })).toBeEnabled();
    expect(within(dialog).queryByRole("button", { name: /Наличные/ })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith(ENDPOINT, expect.objectContaining({ preview: false }));
  });

  it("shows the payment in progress on the tapped method and locks the others", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid } = renderModal();
    let finish: () => void = () => {};
    postMock.mockImplementation(async (_url: string, body: Body) => {
      if (!body.preview) await new Promise<void>((resolve) => (finish = resolve));
      return { data: serve(body) };
    });

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));
    await user.click(within(dialog).getByRole("button", { name: "QR" }));

    expect(within(dialog).getByRole("button", { name: "QR" })).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByRole("button", { name: "QR" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: /Наличные/ })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Назад" })).toBeDisabled();
    finish();
    await waitFor(() => expect(onPaid).toHaveBeenCalled());
  });

  it("keeps a preview error inside the dialog", async () => {
    postMock.mockImplementation(async () => {
      throw apiFailure("no_debt", "У клиента нет долга в USD");
    });
    const { dialog } = renderModal({ currencies: ["USD"] });

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("У клиента нет долга в USD");
    expect(within(dialog).getByRole("button", { name: /Подтвердить/ })).toBeDisabled();
  });

  it("keeps a payment error inside the dialog and does not report success", async () => {
    const user = userEvent.setup();
    const { dialog, onPaid, onClose } = renderModal();
    postMock.mockImplementation(async (_url: string, body: Body) => {
      if (!body.preview) throw apiFailure("payment_in_progress", "По заказу #843 уже идёт оплата");
      return { data: serve(body) };
    });

    await user.type(within(dialog).getByLabelText("Сумма"), "2200000");
    await user.click(await within(dialog).findByRole("button", { name: /^Подтвердить · 2\s200\s000 ₸$/ }));
    await user.click(within(dialog).getByRole("button", { name: /Наличные/ }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("По заказу #843 уже идёт оплата");
    // Отказ не про сумму — окно остаётся на шаге способа.
    expect(dialog).toHaveAccessibleName(/^Как клиент заплатил 2\s200\s000 ₸\?$/);
    expect(within(dialog).getByRole("button", { name: /Наличные/ })).toBeEnabled();
    expect(onPaid).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
