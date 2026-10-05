import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GoodsReturnModal } from "./goods-return-modal";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), permissions: ["orders.edit"] as string[] }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (error: Error) => error.message,
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const OPTIONS = {
  clients: [{ id: 7, name: "Нуржан Сарыагаш", company_name: "", phone: "+7 (700) 000-00-00", currency: "KZT" }],
  products: [{ id: 9, label: "Красный · Красный 50 кг", available_bags: 0, stock_by_warehouse: {} }],
  stores: [],
  departments: [],
  warehouses: [{ id: 1, code: "main", name: "Мельница", address: "", is_active: true, is_default: true }],
};
/** Что клиент может вернуть: DIKHAN в долге (до 120 мешков), KOROL только оплаченный. */
const RETURNABLE = {
  products: [
    { product: 3, label: "Первый сорт DIKHAN 50кг", debt_bags: 120, cash_bags: 0 },
    { product: 4, label: "Второй сорт KOROL 50кг", debt_bags: 0, cash_bags: 4 },
  ],
};
const PLAN = {
  settlement: "debt",
  bags: 50,
  amounts: { KZT: "154000.00" },
  orders: [
    {
      order_id: 903,
      currency: "KZT",
      shipped_at: "2026-10-04T12:00:00+05:00",
      amount: "64000.00",
      lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 20, amount: "64000.00" }],
    },
    {
      order_id: 891,
      currency: "KZT",
      shipped_at: "2026-09-23T12:00:00+05:00",
      amount: "90000.00",
      lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 30, amount: "90000.00" }],
    },
  ],
};

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Нуржан Сарыагаш/ }));
  await user.selectOptions(screen.getByLabelText("Мука 1"), "3");
  await user.type(screen.getByLabelText("Мешков 1"), "50");
}

describe("GoodsReturnModal", () => {
  beforeEach(() => {
    mocks.get
      .mockReset()
      .mockImplementation((url: string) =>
        Promise.resolve({ data: url.startsWith("/orders/form-options/") ? OPTIONS : RETURNABLE }),
      );
    mocks.post.mockReset();
    mocks.permissions = ["orders.edit"];
  });

  it("сначала раскладка с сервера по заказам, потом подтверждение", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    mocks.post.mockResolvedValueOnce({ data: PLAN }).mockResolvedValueOnce({ data: { ...PLAN, return_id: 12 } });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={onDone} />);

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    const plan = await screen.findByRole("region", { name: "Раскладка возврата" });
    expect(within(plan).getByText(/#903 · отгружен 04\.10/)).toBeInTheDocument();
    expect(plan).toHaveTextContent("20 мешков");
    expect(plan).toHaveTextContent("Итого 50 мешков");
    expect(mocks.post).toHaveBeenLastCalledWith("/clients/7/goods-return/", {
      settlement: "debt",
      warehouse: 1,
      lines: [{ product: 3, bags: 50 }],
      preview: true,
    });

    await user.click(screen.getByRole("button", { name: "Подтвердить возврат" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenLastCalledWith(
      "/clients/7/goods-return/",
      expect.objectContaining({ preview: false }),
    );
  });

  it("«Деньги из кассы» — только с правом возврата оплаты", async () => {
    const { unmount } = render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);
    expect(await screen.findByRole("radio", { name: "Деньги из кассы" })).toBeDisabled();
    unmount();

    mocks.permissions = ["orders.edit", "payments.confirm"];
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);
    expect(await screen.findByRole("radio", { name: "Деньги из кассы" })).toBeEnabled();
  });

  it("ошибку сервера показывает в окне, а правка сбрасывает раскладку", async () => {
    const user = userEvent.setup();
    mocks.post
      .mockRejectedValueOnce(new Error("Максимум 20 мешков «Первый сорт DIKHAN 50кг» в счёт долга"))
      .mockResolvedValueOnce({ data: PLAN });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Проверить" }));
    expect(await screen.findByText(/Максимум 20 мешков/)).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Мешков 1"));
    await user.type(screen.getByLabelText("Мешков 1"), "20");
    expect(screen.queryByText(/Максимум 20 мешков/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Проверить" }));
    await screen.findByRole("region", { name: "Раскладка возврата" });

    await user.type(screen.getByLabelText("Мешков 1"), "0");
    expect(screen.queryByRole("region", { name: "Раскладка возврата" })).toBeNull();
    expect(screen.getByRole("button", { name: "Проверить" })).toBeEnabled();
  });

  it("в списке — только мука клиента, под строкой — сколько можно вернуть", async () => {
    const user = userEvent.setup();
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    expect(await screen.findByLabelText("Мука 1")).toBeDisabled();
    await user.click(await screen.findByRole("button", { name: /Нуржан Сарыагаш/ }));

    const select = screen.getByLabelText("Мука 1");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Выберите муку", "Первый сорт DIKHAN 50кг", "Второй сорт KOROL 50кг"]);
    expect(mocks.get).toHaveBeenCalledWith("/clients/7/goods-return/", expect.anything());
    await user.selectOptions(select, "3");
    expect(screen.getByText("Можно вернуть до 120 мешков в счёт долга")).toBeInTheDocument();
    await user.selectOptions(select, "4");
    expect(screen.getByText("Нет заказов в долге с этой мукой — выберите «Деньги из кассы»")).toBeInTheDocument();
  });

  it("пустая лишняя строка не мешает, двойное нажатие проводит возврат один раз", async () => {
    const user = userEvent.setup();
    let finish: (value: unknown) => void = () => undefined;
    mocks.post
      .mockResolvedValueOnce({ data: PLAN })
      .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await fillForm(user);
    await user.click(screen.getByRole("button", { name: "Ещё мука" }));
    await user.click(screen.getByRole("button", { name: "Проверить" }));
    const confirm = await screen.findByRole("button", { name: "Подтвердить возврат" });
    confirm.click();
    confirm.click();
    finish({ data: { ...PLAN, return_id: 12 } });

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post).toHaveBeenNthCalledWith(
      1,
      "/clients/7/goods-return/",
      expect.objectContaining({
        lines: [{ product: 3, bags: 50 }],
      }),
    );
  });

  it("валюта — из заказов: суммы в своей валюте, итог по валютам не складывается", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({
      data: {
        settlement: "cash",
        bags: 12,
        amounts: { USD: "200.00", KZT: "2000.00" },
        orders: [
          {
            order_id: 5,
            currency: "USD",
            shipped_at: "2026-10-04T12:00:00+05:00",
            amount: "200.00",
            lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 10, amount: "200.00" }],
          },
          {
            order_id: 4,
            currency: "KZT",
            shipped_at: "2026-10-03T12:00:00+05:00",
            amount: "2000.00",
            lines: [{ label: "Первый сорт DIKHAN 50кг", bags: 2, amount: "2000.00" }],
          },
        ],
      },
    });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await fillForm(user);
    expect(screen.queryByRole("radiogroup", { name: "Валюта" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Проверить" }));

    const plan = await screen.findByRole("region", { name: "Раскладка возврата" });
    expect(plan).toHaveTextContent("Итого 12 мешковКасса отдаёт: 200 $ и 2 000 ₸");
  });
});
