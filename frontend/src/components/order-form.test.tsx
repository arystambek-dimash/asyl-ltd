import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderForm } from "@/components/order-form";
import type { Client, Department, Order, Product } from "@/lib/types";

const useApiMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());
const patchMock = vi.hoisted(() => vi.fn());
const postMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
const meMock = vi.hoisted(() => ({ current: { sales_department: null, permissions: [] as string[] } }));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: meMock.current }),
}));
vi.mock("@/lib/api", () => ({
  api: { patch: patchMock, post: postMock },
  apiError: () => "Ошибка сохранения",
}));

const client = {
  id: 1,
  name: "Тестовый клиент",
  company_name: "",
  phone: "",
  currency: "KZT",
} as Client;

const product = {
  id: 2,
  label: "Мука 50 кг",
  available_bags: 20,
} as Product;

const department = {
  id: 3,
  code: "sales",
  name: "Продажи",
  color: "#111111",
  is_default: true,
} as Department;

function apiState<T>(
  data: T | null,
  {
    loading = false,
    error = "",
    reload = vi.fn(),
  }: { loading?: boolean; error?: string; reload?: ReturnType<typeof vi.fn> } = {},
) {
  return { data, loading, error, reload, setData: vi.fn() };
}

describe("OrderForm reference data resilience", () => {
  beforeEach(() => {
    useApiMock.mockReset();
    pushMock.mockReset();
    patchMock.mockReset();
    postMock.mockReset();
    patchMock.mockResolvedValue({ data: {} });
    postMock.mockResolvedValue({ data: { id: 1 } });
    meMock.current = { sales_department: null, permissions: [] };
  });

  it("shows a lookup error and blocks progression until all required data is available", async () => {
    const reloadFormOptions = vi.fn();
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState(null, {
          error: "Доступ запрещён",
          reload: reloadFormOptions,
        }),
      ],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm onCancel={vi.fn()} onDone={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Доступ запрещён");
    expect(screen.getByRole("button", { name: /Создать заказ/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Повторить/ }));
    expect(reloadFormOptions).toHaveBeenCalledOnce();
  });

  it("keeps manual prices editable when the client price list fails and offers retry", async () => {
    const reloadClientPrices = vi.fn();
    const template = {
      id: 9,
      client: client.id,
      department: department.code,
      currency: "KZT",
      truck_number: "",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [client],
          products: [product],
          stores: [],
          departments: [department],
        }),
      ],
      [
        "/client-prices/?client=1&currency=KZT",
        apiState<Record<string, string>>(null, {
          error: "Сеть недоступна",
          reload: reloadClientPrices,
        }),
      ],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);

    const price = screen.getByRole("spinbutton", { name: "Цена, позиция 1" });
    expect(price).toHaveValue(17.5);
    expect(screen.getByRole("alert")).toHaveTextContent("Цены можно ввести вручную");

    await user.clear(price);
    await user.type(price, "19");
    await user.click(screen.getByRole("button", { name: /Повторить/ }));

    expect(price).toHaveValue(19);
    expect(reloadClientPrices).toHaveBeenCalledOnce();
  });

  it("does not create an order while changing currency and repricing", async () => {
    const template = {
      id: 12,
      client: client.id,
      department: department.code,
      currency: "KZT",
      truck_number: "",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [client],
          products: [product],
          stores: [],
          departments: [department],
        }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState<Record<string, string>>({ "2": "17.50" })],
      ["/client-prices/?client=1&currency=USD", apiState<Record<string, string>>({ "2": "4.25" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);

    await user.click(screen.getByRole("radio", { name: /Доллары/ }));
    expect(postMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: "Цена, позиция 1" })).toHaveValue(4.25));
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));

    expect(postMock).toHaveBeenCalledOnce();
    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({
        currency: "USD",
        prices: { "2": "4.25" },
      }),
    );
  });

  it("uses per-warehouse balances when the same product is stored in multiple warehouses", async () => {
    const secondaryProduct = {
      id: 4,
      label: "Мука со второго склада 50 кг",
      available_bags: 14,
      warehouse: 22,
      warehouse_name: "Склад №2",
    } as Product;
    const template = {
      id: 13,
      client: client.id,
      department: department.code,
      currency: "KZT",
      warehouse: 11,
      warehouse_name: "Основной склад",
      truck_number: "",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [client],
          products: [
            {
              ...product,
              warehouse: 11,
              warehouse_name: "Основной склад",
              stock_by_warehouse: { "11": 20, "22": 7 },
            },
            { ...secondaryProduct, stock_by_warehouse: { "22": 14 } },
          ],
          stores: [],
          departments: [department],
          warehouses: [
            { id: 11, code: "main", name: "Основной склад", address: "", is_active: true, is_default: true },
            { id: 22, code: "second", name: "Склад №2", address: "Цех 2", is_active: true, is_default: false },
          ],
        }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState<Record<string, string>>({ "2": "17.50", "4": "21.00" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);
    await user.selectOptions(screen.getByLabelText("Склад отгрузки"), "22");

    expect(screen.getByRole("option", { name: "Мука 50 кг · 7 меш." })).toBeInTheDocument();
    expect(screen.queryByText("Цех 2")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));

    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({
        warehouse: 22,
        items: [{ product: 2, quantity: 3 }],
      }),
    );
  });

  it("keeps an inactive pinned warehouse visible while editing an order", async () => {
    const editing = {
      id: 14,
      client: client.id,
      department: department.code,
      currency: "KZT",
      status: "confirmed",
      warehouse: 99,
      warehouse_name: "Старый склад",
      transport_type: "truck",
      truck_number: "",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/orders/form-options/") {
        return apiState({
          clients: [client],
          products: [{ ...product, warehouse: 99, warehouse_name: "Старый склад" }],
          stores: [],
          departments: [department],
          warehouses: [
            { id: 11, code: "main", name: "Основной склад", address: "", is_active: true, is_default: true },
          ],
        });
      }
      return apiState(null);
    });

    render(<OrderForm editing={editing} onCancel={vi.fn()} onDone={vi.fn()} />);

    const selector = screen.getByLabelText("Склад отгрузки");
    expect(selector).toBeDisabled();
    expect(selector).toHaveValue("99");
    expect(screen.getByRole("option", { name: "Старый склад · отключён" })).toBeInTheDocument();
  });

  it("uses the active default instead of an inactive warehouse from a template", async () => {
    const template = {
      id: 15,
      client: client.id,
      department: department.code,
      currency: "KZT",
      warehouse: 99,
      warehouse_name: "Старый склад",
      truck_number: "",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/orders/form-options/") {
        return apiState({
          clients: [client],
          products: [{ ...product, warehouse: 99, warehouse_name: "Старый склад" }],
          stores: [],
          departments: [department],
          warehouses: [
            { id: 11, code: "main", name: "Основной склад", address: "", is_active: true, is_default: true },
          ],
        });
      }
      return apiState(null);
    });

    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);

    await waitFor(() => expect(screen.getByLabelText("Склад отгрузки")).toHaveValue("11"));
    expect(screen.getByLabelText("Товар, позиция 1")).toHaveValue("");
  });

  it("requires an audit reason and sends it when a shipped order is corrected", async () => {
    const editing = {
      id: 10,
      client: client.id,
      client_name: client.name,
      department: department.code,
      currency: "KZT",
      status: "shipped",
      transport_type: "truck",
      truck_number: "123ABC02",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [client],
          products: [{ ...product, available_bags: 0 }],
          stores: [],
          departments: [department],
        }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState<Record<string, string>>({})],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm editing={editing} onCancel={vi.fn()} onDone={vi.fn()} />);

    expect(screen.getByRole("option", { name: /нет остатка, но доступен/ })).toBeEnabled();
    const save = screen.getByRole("button", { name: /Сохранить изменения/ });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText("Причина корректировки отгруженного заказа"), "Исправили факт");
    expect(save).toBeEnabled();
    await user.click(save);

    expect(patchMock).toHaveBeenCalledWith(
      "/orders/10/",
      expect.objectContaining({
        edit_reason: "Исправили факт",
        items: [{ product: 2, quantity: 3 }],
        prices: { "2": "17.50" },
      }),
    );
  });

  it("keeps the edit form usable while loading but omits the frozen composition", async () => {
    const editing = {
      id: 11,
      client: client.id,
      client_name: client.name,
      department: department.code,
      currency: "KZT",
      status: "loading",
      transport_type: "truck",
      truck_number: "123ABC02",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({ clients: [client], products: [product], stores: [], departments: [department] }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState<Record<string, string>>({})],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));

    const user = userEvent.setup();
    render(<OrderForm editing={editing} onCancel={vi.fn()} onDone={vi.fn()} />);

    expect(screen.getByLabelText("Товар, позиция 1")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    const body = patchMock.mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("items");
    expect(body).not.toHaveProperty("prices");
  });

  function numberOrder(overrides: Partial<Order> = {}): Order {
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({ clients: [client], products: [product], stores: [], departments: [department] }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState({ "2": "17.50" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));
    return {
      id: 22,
      client: client.id,
      department: department.code,
      currency: "KZT",
      status: "pending",
      transport_type: "train",
      truck_number: "00123456",
      items: [{ product: product.id, quantity: 3, unit_price: "17.50" }],
      total_amount: "52.50",
      paid_total: "0",
      is_fully_paid: false,
      debt_override: false,
      created_at: "2026-09-07",
      ...overrides,
    };
  }

  it("creates a wagon order with its complete number from a template", async () => {
    const user = userEvent.setup();
    render(<OrderForm template={numberOrder()} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Номер вагона")).toHaveValue("00123456");
    await user.clear(screen.getByLabelText("Номер вагона"));
    await user.type(screen.getByLabelText("Номер вагона"), "00012345");
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));
    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({ transport_type: "train", truck_number: "00012345" }),
    );
    // У вагона прицепа нет.
    expect(postMock.mock.calls[0][1]).not.toHaveProperty("trailer_number");
  });

  it("sends the truck and the trailer and hints the plate country of the client", async () => {
    const template = numberOrder({ transport_type: "truck", truck_number: "" });
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [{ ...client, country: "Кыргызстан" }],
          products: [product],
          stores: [],
          departments: [department],
        }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState({ "2": "17.50" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));
    const user = userEvent.setup();
    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);

    expect(screen.getByLabelText("Страна тягача")).toHaveValue("KG");
    await user.type(screen.getByLabelText("Тягач"), "07kg695adt");
    await user.type(screen.getByLabelText("Прицеп (необязательно)"), "07 kg 837 pb");
    expect(screen.getByLabelText("Тягач")).toHaveValue("07 KG 695 ADT");
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));

    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({ transport_type: "truck", truck_number: "07KG695ADT", trailer_number: "07KG837PB" }),
    );
  });

  it("keeps a legacy truck number untouched on an unrelated edit", async () => {
    const user = userEvent.setup();
    render(
      <OrderForm
        editing={numberOrder({ transport_type: "truck", truck_number: "самовывоз", trailer_number: "" })}
        onCancel={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Тягач")).toHaveValue("самовывоз");
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(patchMock).toHaveBeenCalledWith(
      "/orders/22/",
      expect.objectContaining({ transport_type: "truck", truck_number: "самовывоз", trailer_number: "" }),
    );
  });

  it("keeps a wagon number on edit and preserves drafts when switching transport", async () => {
    const user = userEvent.setup();
    render(<OrderForm editing={numberOrder()} onCancel={vi.fn()} onDone={vi.fn()} />);
    await user.click(screen.getByRole("radio", { name: /Трак/ }));
    await user.click(screen.getByRole("radio", { name: /Вагон/ }));
    expect(screen.getByLabelText("Номер вагона")).toHaveValue("00123456");
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(patchMock).toHaveBeenCalledWith(
      "/orders/22/",
      expect.objectContaining({ transport_type: "train", truck_number: "00123456" }),
    );
  });

  it("explains an incomplete wagon number and permits leaving it unknown", async () => {
    const user = userEvent.setup();
    render(<OrderForm editing={numberOrder({ truck_number: "" })} onCancel={vi.fn()} onDone={vi.fn()} />);
    await user.type(screen.getByLabelText("Номер вагона"), "123");
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(screen.getByText(/Номер вагона должен содержать 8 цифр/)).toBeInTheDocument();
    expect(patchMock).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText("Номер вагона"));
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(patchMock).toHaveBeenCalledWith("/orders/22/", expect.objectContaining({ truck_number: "" }));
  });

  it("keeps the loaded wagon number disabled and out of an unrelated patch", async () => {
    const user = userEvent.setup();
    render(<OrderForm editing={numberOrder({ status: "loading" })} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Номер вагона")).toBeDisabled();
    expect(screen.getByLabelText("Номер вагона")).toHaveValue("00123456");
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(patchMock.mock.calls[0][1]).not.toHaveProperty("truck_number");
  });

  it("allows editing an unrelated field while preserving a legacy wagon number", async () => {
    const user = userEvent.setup();
    render(<OrderForm editing={numberOrder({ truck_number: "1234567" })} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Номер вагона")).toHaveValue("1234567");
    await user.type(screen.getByLabelText("Плановая дата прибытия"), "2026-09-08");
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    expect(patchMock).toHaveBeenCalledWith(
      "/orders/22/",
      expect.objectContaining({ transport_type: "train", truck_number: "1234567", arrival_date: "2026-09-08" }),
    );
  });

  it("requires a valid new wagon number when copying a legacy template", async () => {
    const user = userEvent.setup();
    render(<OrderForm template={numberOrder({ truck_number: "1234567" })} onCancel={vi.fn()} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));
    expect(screen.getByText(/Номер вагона должен содержать 8 цифр/)).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });
  it("creates a template order in the selected client's department", async () => {
    const template = {
      id: 99,
      client: client.id,
      department: "old",
      currency: "KZT",
      truck_number: "",
      items: [{ product: product.id, quantity: 2, unit_price: "17.50" }],
    } as Order;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({
          clients: [{ ...client, department_code: department.code, department_name: department.name }],
          products: [product],
          stores: [],
          departments: [department, { ...department, id: 4, code: "old", name: "Другой отдел" }],
        }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState({ "2": "17.50" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));
    const user = userEvent.setup();
    render(<OrderForm template={template} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByText(/отдел клиента/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Другой отдел" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));
    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({ client: client.id, department: department.code }),
    );
  });

  it("hides the backdate block without orders.edit and sends it when enabled", async () => {
    meMock.current = { sales_department: null, permissions: ["orders.edit", "payments.create"] };
    const user = userEvent.setup();
    render(
      <OrderForm
        template={numberOrder({ transport_type: "truck", truck_number: "" })}
        onCancel={vi.fn()}
        onDone={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "Задним числом" }));
    const date = screen.getByLabelText("Дата");
    await user.clear(date);
    await user.type(date, "2026-09-10");
    await user.click(screen.getByRole("radio", { name: /Отгружено/ }));
    await user.click(screen.getByRole("checkbox", { name: /Оплачен полностью/ }));
    await user.click(screen.getByRole("radio", { name: "Kaspi" }));
    await user.click(screen.getByRole("button", { name: /Создать задним числом/ }));

    expect(postMock).toHaveBeenCalledWith(
      "/orders/",
      expect.objectContaining({
        backdate: { date: "2026-09-10", status: "shipped", paid: true, payment_method: "kaspi" },
      }),
    );
  });

  describe("«Оплата сразу»", () => {
    const cashierManager = {
      sales_department: null,
      permissions: ["payments.create", "orders.confirm", "orders.edit"],
    };
    const truckTemplate = () => numberOrder({ transport_type: "truck", truck_number: "" });
    const confirmedOrder = {
      id: 30,
      status: "confirmed",
      payment_open: true,
      payment_open_methods: ["cash", "kaspi", "remote"],
    };

    it("creates the order and takes the prepayment with the receive-payment body", async () => {
      meMock.current = cashierManager;
      postMock.mockImplementation(async (url: string) => ({ data: url === "/orders/" ? confirmedOrder : {} }));
      const user = userEvent.setup();
      render(<OrderForm template={truckTemplate()} onCancel={vi.fn()} onDone={vi.fn()} />);

      await user.click(screen.getByRole("checkbox", { name: "Оплата сразу" }));
      // Сумма по умолчанию — итог заказа: 3 × 17,50.
      expect(screen.getByLabelText("Сумма оплаты")).toHaveValue(52.5);
      await user.click(screen.getByRole("button", { name: /Kaspi-терминал/ }));
      await user.click(screen.getByRole("button", { name: /Создать и принять оплату/ }));

      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/orders/30"));
      expect(postMock).toHaveBeenNthCalledWith(
        1,
        "/orders/",
        expect.not.objectContaining({ backdate: expect.anything() }),
      );
      expect(postMock).toHaveBeenNthCalledWith(2, "/orders/30/payments/", {
        amount: "52.5",
        method: "kaspi",
        stage: "received",
      });
    });

    it("keeps the amount on the order total until the cashier edits it", async () => {
      meMock.current = cashierManager;
      const user = userEvent.setup();
      render(<OrderForm template={truckTemplate()} onCancel={vi.fn()} onDone={vi.fn()} />);

      await user.click(screen.getByRole("checkbox", { name: "Оплата сразу" }));
      const quantity = screen.getByRole("spinbutton", { name: "Количество мешков, позиция 1" });
      await user.clear(quantity);
      await user.type(quantity, "4");
      expect(screen.getByLabelText("Сумма оплаты")).toHaveValue(70);

      await user.clear(screen.getByLabelText("Сумма оплаты"));
      await user.type(screen.getByLabelText("Сумма оплаты"), "20");
      await user.clear(quantity);
      await user.type(quantity, "5");
      expect(screen.getByLabelText("Сумма оплаты")).toHaveValue(20);

      await user.clear(screen.getByLabelText("Сумма оплаты"));
      await user.type(screen.getByLabelText("Сумма оплаты"), "1000");
      expect(screen.getByText("Сумма больше остатка к оплате.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Создать и принять оплату/ })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Весь итог" }));
      expect(screen.getByLabelText("Сумма оплаты")).toHaveValue(87.5);
    });

    it("opens the order with a payment retry when the server rejects the prepayment", async () => {
      meMock.current = cashierManager;
      postMock.mockImplementation(async (url: string) => {
        if (url === "/orders/") return { data: confirmedOrder };
        throw { response: { status: 400, data: { detail: "Сумма больше остатка" } } };
      });
      const user = userEvent.setup();
      render(<OrderForm template={truckTemplate()} onCancel={vi.fn()} onDone={vi.fn()} />);

      await user.click(screen.getByRole("checkbox", { name: "Оплата сразу" }));
      await user.click(screen.getByRole("button", { name: /Создать и принять оплату/ }));
      await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/orders/30?pay=cash&amount=52.5"));
    });

    it("is offered only to staff who confirm orders and take payments, and not with a backdate", async () => {
      meMock.current = { sales_department: null, permissions: ["payments.create", "orders.edit"] };
      const { unmount } = render(<OrderForm template={truckTemplate()} onCancel={vi.fn()} onDone={vi.fn()} />);
      expect(screen.queryByRole("checkbox", { name: "Оплата сразу" })).not.toBeInTheDocument();
      unmount();

      meMock.current = cashierManager;
      const user = userEvent.setup();
      render(<OrderForm template={truckTemplate()} onCancel={vi.fn()} onDone={vi.fn()} />);
      await user.click(screen.getByRole("checkbox", { name: "Задним числом" }));
      expect(screen.queryByRole("checkbox", { name: "Оплата сразу" })).not.toBeInTheDocument();
    });

    it("is not offered while editing an order", () => {
      meMock.current = cashierManager;
      render(<OrderForm editing={numberOrder()} onCancel={vi.fn()} onDone={vi.fn()} />);
      expect(screen.queryByRole("checkbox", { name: "Оплата сразу" })).not.toBeInTheDocument();
    });
  });

  it("does not offer backdating without the edit permission", () => {
    render(<OrderForm template={numberOrder()} onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByRole("checkbox", { name: "Задним числом" })).not.toBeInTheDocument();
  });

  it("rejects a backdate in the future before sending", async () => {
    meMock.current = { sales_department: null, permissions: ["orders.edit"] };
    const user = userEvent.setup();
    render(
      <OrderForm
        template={numberOrder({ transport_type: "truck", truck_number: "" })}
        onCancel={vi.fn()}
        onDone={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("checkbox", { name: "Задним числом" }));
    const date = screen.getByLabelText("Дата");
    await user.clear(date);
    await user.type(date, "2099-01-01");
    expect(screen.getByRole("button", { name: /Создать задним числом/ })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: /Оплачен полностью/ })).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });
});

describe("OrderForm draft", () => {
  beforeEach(() => {
    useApiMock.mockReset();
    postMock.mockReset();
    postMock.mockResolvedValue({ data: { id: 77 } });
    localStorage.clear();
    meMock.current = { id: 5, sales_department: null, permissions: [] } as typeof meMock.current;
    const states = new Map<string, unknown>([
      [
        "/orders/form-options/",
        apiState({ clients: [client], products: [product], stores: [], departments: [department] }),
      ],
      ["/client-prices/?client=1&currency=KZT", apiState<Record<string, string>>({ "2": "10" })],
    ]);
    useApiMock.mockImplementation((url: string | null) => states.get(url ?? "") ?? apiState(null));
  });

  it("restores typed data after the form is closed and clears the draft once the order is created", async () => {
    const user = userEvent.setup();
    const onDraftChange = vi.fn();
    const first = render(<OrderForm onCancel={vi.fn()} onDone={vi.fn()} onDraftChange={onDraftChange} />);
    await user.click(screen.getByRole("button", { name: /Тестовый клиент/ }));
    await user.type(screen.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }), "4");
    expect(onDraftChange).toHaveBeenLastCalledWith(true);
    first.unmount();

    render(<OrderForm onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Изменить" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Количество мешков, позиция 1" })).toHaveValue(4);

    await user.selectOptions(screen.getByRole("combobox", { name: "Товар, позиция 1" }), "2");
    await user.selectOptions(screen.getByRole("combobox", { name: "Отдел продаж" }), "sales");
    await user.click(screen.getByRole("button", { name: /Создать заказ/ }));
    await waitFor(() => expect(postMock).toHaveBeenCalledOnce());
    expect(localStorage.getItem("asyl_order_draft_v1:5")).toBeNull();
  });

  it("restores the trailer, brings «Оплата сразу» back switched off and clears the draft before taking the prepayment", async () => {
    meMock.current = {
      id: 5,
      sales_department: null,
      permissions: ["payments.create", "orders.confirm"],
    } as typeof meMock.current;
    const draftKey = "asyl_order_draft_v1:5";
    let draftWhenPaying: string | null | undefined;
    postMock.mockImplementation(async (url: string) => {
      if (url === "/orders/") {
        return {
          data: { id: 30, status: "confirmed", payment_open: true, payment_open_methods: ["cash", "kaspi", "remote"] },
        };
      }
      draftWhenPaying = localStorage.getItem(draftKey);
      return { data: {} };
    });
    const user = userEvent.setup();
    const first = render(<OrderForm onCancel={vi.fn()} onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Тестовый клиент/ }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Товар, позиция 1" }), "2");
    await user.type(screen.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }), "4");
    await user.selectOptions(screen.getByRole("combobox", { name: "Отдел продаж" }), "sales");
    await user.type(screen.getByLabelText("Тягач"), "07kg695adt");
    await user.type(screen.getByLabelText("Прицеп (необязательно)"), "07 kg 837 pb");
    await user.click(screen.getByRole("checkbox", { name: "Оплата сразу" }));
    await user.click(screen.getByRole("button", { name: /Kaspi-терминал/ }));
    await user.clear(screen.getByLabelText("Сумма оплаты"));
    await user.type(screen.getByLabelText("Сумма оплаты"), "25");
    first.unmount();

    render(<OrderForm onCancel={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByLabelText("Прицеп (необязательно)")).toHaveValue("07 KG 837 PB");
    // После восстановления «Оплата сразу» выключена: деньги не запишутся по черновику сами.
    expect(screen.getByRole("checkbox", { name: "Оплата сразу" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Создать заказ/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Создать и принять оплату/ })).not.toBeInTheDocument();

    // Включают снова руками — способ и сумма черновика на месте.
    await user.click(screen.getByRole("checkbox", { name: "Оплата сразу" }));
    expect(screen.getByRole("button", { name: /Kaspi-терминал/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Сумма оплаты")).toHaveValue(25);

    await user.click(screen.getByRole("button", { name: /Создать и принять оплату/ }));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/orders/30"));
    expect(postMock).toHaveBeenNthCalledWith(
      1,
      "/orders/",
      expect.objectContaining({ truck_number: "07KG695ADT", trailer_number: "07KG837PB" }),
    );
    expect(postMock).toHaveBeenNthCalledWith(2, "/orders/30/payments/", {
      amount: "25",
      method: "kaspi",
      stage: "received",
    });
    // Заказ уже создан — черновик удалён до приёма оплаты, повтор идёт с карточки заказа.
    expect(draftWhenPaying).toBeNull();
    expect(localStorage.getItem(draftKey)).toBeNull();
  });
});
