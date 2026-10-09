import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GoodsReturn } from "@/lib/types";
import { GoodsReturnModal } from "./goods-return-modal";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get, post: mocks.post },
  apiError: (error: Error) => error.message,
}));

function product(id: number, name: string, stock: Record<string, number>) {
  return {
    id,
    name,
    label: `${name} · Синий 50 кг`,
    weight_kg: "50.00",
    codes: [],
    color_label: "Синий",
    available_bags: stock["1"] ?? 0,
    stock_by_warehouse: stock,
  };
}

/** Весь действующий каталог из формы заказа: вернуть можно любой товар, даже без остатка. */
const OPTIONS = {
  clients: [{ id: 7, name: "Нуржан Сарыагаш", company_name: "", phone: "+7 (700) 000-00-00", currency: "KZT" }],
  products: [
    product(3, "Первый сорт DIKHAN 50кг", { "1": 120, "2": 5 }),
    product(4, "Второй сорт KOROL 50кг", { "1": 0 }),
    product(5, "Высший сорт OMAD 50кг", {}),
  ],
  stores: [],
  departments: [],
  warehouses: [
    { id: 1, code: "main", name: "Мельница", address: "", is_active: true, is_default: true },
    { id: 2, code: "newcity", name: "Нью-Сити", address: "", is_active: true, is_default: false },
  ],
};

/** Ответ создания — строка «Заказы → Возвраты»: ждёт приёмки, без заказов и денег. */
function createdRow(fields: Partial<GoodsReturn> = {}): GoodsReturn {
  return {
    id: 12,
    created_at: "2026-10-09T15:00:00+05:00",
    client_name: "Нуржан Сарыагаш",
    warehouse_name: "Мельница",
    created_by_name: "Иван Петров",
    status: "pending",
    status_label: "Ждёт приёмки",
    accepted_by_name: null,
    accepted_at: null,
    items: [{ id: 1, product_label: "Второй сорт KOROL 50кг", bags: 12, accepted_bags: null }],
    settlement_label: null,
    amounts: {},
    lines: [],
    ...fields,
  };
}

const createButton = () => screen.getByRole("button", { name: "Создать возврат" });

async function chooseClient(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Нуржан Сарыагаш/ }));
}

async function chooseProduct(user: ReturnType<typeof userEvent.setup>, row: number, name: RegExp) {
  await user.click(screen.getByRole("button", { name: new RegExp(`^Товар ${row}`) }));
  await user.click(within(screen.getByRole("listbox", { name: "Товары" })).getByRole("option", { name }));
}

describe("GoodsReturnModal", () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue({ data: OPTIONS });
    mocks.post.mockReset();
  });

  it("создаёт возврат с любым товаром каталога — он ждёт приёмки у кладовщика", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    mocks.post.mockResolvedValueOnce({ data: createdRow() });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={onDone} />);

    await chooseClient(user);
    // KOROL клиент мог и не покупать, остатка нет — вернуть всё равно можно.
    await chooseProduct(user, 1, /KOROL/);
    await user.type(screen.getByLabelText("Мешков 1"), "12");
    expect(screen.getByText(/Итого/)).toHaveTextContent("Итого 12 мешков");
    await user.click(createButton());

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith("/clients/7/goods-return/", {
      warehouse: 1,
      lines: [{ product: 4, bags: 12 }],
    });
    const created = await screen.findByRole("status");
    expect(created).toHaveTextContent("Возврат №12 создан");
    expect(created).toHaveTextContent("Ждёт приёмки у кладовщика — принятые мешки лягут на склад «Мельница»");
    expect(onDone).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Готово" }));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("без денег: ни выбора «долг или касса», ни раскладки по заказам, товары — из каталога формы", async () => {
    const user = userEvent.setup();
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await chooseClient(user);
    expect(screen.queryByRole("radiogroup", { name: "Деньги" })).toBeNull();
    expect(screen.queryByText(/Уменьшить долг|Деньги из кассы/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Проверить" })).toBeNull();
    expect(screen.getByText("Долг и касса не меняются.", { exact: false })).toBeInTheDocument();

    // Список — весь действующий каталог, остаток — на выбранном складе.
    await user.click(screen.getByRole("button", { name: /^Товар 1/ }));
    const options = within(screen.getByRole("listbox", { name: "Товары" })).getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options.every((option) => !option.hasAttribute("disabled"))).toBe(true);
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: /DIKHAN/ })).toHaveTextContent("120 меш.");

    // Справочники — только форма заказа: отдельного списка «что клиент может вернуть» нет.
    expect(mocks.get.mock.calls.map(([url]) => url)).toEqual(["/orders/form-options/"]);
  });

  it("«Создать возврат» доступна, когда выбраны клиент, товар и мешки; пустая лишняя строка не мешает", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({ data: createdRow() });
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await screen.findByRole("button", { name: /Нуржан Сарыагаш/ });
    expect(createButton()).toBeDisabled();
    expect(screen.getByText("Выберите клиента")).toBeInTheDocument();

    await chooseClient(user);
    expect(screen.getByText("Выберите товар и сколько мешков")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Мешков 1"), "1a5");
    expect(screen.getByLabelText("Мешков 1")).toHaveValue("15");
    expect(screen.getByText("Выберите товар в каждой строке")).toBeInTheDocument();
    await chooseProduct(user, 1, /DIKHAN/);
    expect(createButton()).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Ещё товар" }));
    expect(createButton()).toBeEnabled();
    await chooseProduct(user, 2, /OMAD/);
    expect(createButton()).toBeDisabled();
    expect(screen.getByText("Укажите мешки в каждой строке")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Мешков 2"), "3");

    await user.selectOptions(screen.getByLabelText("Склад, где кладовщик примет мешки"), "2");
    await user.click(createButton());
    expect(mocks.post).toHaveBeenCalledWith("/clients/7/goods-return/", {
      warehouse: 2,
      lines: [
        { product: 3, bags: 15 },
        { product: 5, bags: 3 },
      ],
    });
  });

  it("строку можно убрать; последняя — очищается", async () => {
    const user = userEvent.setup();
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await chooseClient(user);
    await chooseProduct(user, 1, /DIKHAN/);
    await user.type(screen.getByLabelText("Мешков 1"), "4");
    await user.click(screen.getByRole("button", { name: "Ещё товар" }));
    await user.click(screen.getByRole("button", { name: "Убрать строку 1" }));
    expect(screen.queryByLabelText("Мешков 2")).toBeNull();
    expect(screen.getByLabelText("Мешков 1")).toHaveValue("");

    await user.type(screen.getByLabelText("Мешков 1"), "9");
    await user.click(screen.getByRole("button", { name: "Убрать строку 1" }));
    expect(screen.getByLabelText("Мешков 1")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Товар 1" })).toBeInTheDocument();
  });

  it("ошибку сервера показывает в окне, правка её убирает", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValueOnce(new Error("Товар «Высший сорт OMAD 50кг» в архиве"));
    render(<GoodsReturnModal open onClose={vi.fn()} onDone={vi.fn()} />);

    await chooseClient(user);
    await chooseProduct(user, 1, /OMAD/);
    await user.type(screen.getByLabelText("Мешков 1"), "2");
    await user.click(createButton());

    expect(await screen.findByText("Товар «Высший сорт OMAD 50кг» в архиве")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    await user.type(screen.getByLabelText("Мешков 1"), "0");
    expect(screen.queryByText(/в архиве/)).toBeNull();
  });

  it("двойное нажатие создаёт возврат один раз, закрытие после создания перечитывает «Возвраты»", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onDone = vi.fn();
    let finish: (value: unknown) => void = () => undefined;
    mocks.post.mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
    render(<GoodsReturnModal open onClose={onClose} onDone={onDone} />);

    await chooseClient(user);
    await chooseProduct(user, 1, /DIKHAN/);
    await user.type(screen.getByLabelText("Мешков 1"), "50");
    const create = createButton();
    create.click();
    create.click();
    finish({ data: createdRow({ id: 14 }) });

    expect(await screen.findByRole("status")).toHaveTextContent("Возврат №14 создан");
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });
});
