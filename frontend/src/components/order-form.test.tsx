import { render, screen } from "@testing-library/react";
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
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { sales_department: null } }),
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
    expect(screen.getByRole("button", { name: /Продолжить/ })).toBeDisabled();

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

    await user.click(screen.getByRole("button", { name: /Продолжить/ }));
    await user.click(screen.getByRole("button", { name: /Продолжить/ }));

    const price = screen.getByRole("spinbutton", { name: "Цена, позиция 1" });
    expect(price).toHaveValue(17.5);
    expect(screen.getByRole("alert")).toHaveTextContent("Цены можно ввести вручную");

    await user.clear(price);
    await user.type(price, "19");
    await user.click(screen.getByRole("button", { name: /Повторить/ }));

    expect(price).toHaveValue(19);
    expect(reloadClientPrices).toHaveBeenCalledOnce();
  });

  it("does not create an order while advancing or going back to change currency", async () => {
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

    await user.click(screen.getByRole("button", { name: /Продолжить/ }));
    const currencyStepContinue = screen.getByRole("button", { name: /Продолжить/ });
    await user.click(currencyStepContinue);

    expect(postMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Создать заказ/ })).not.toBe(currencyStepContinue);
    await user.click(screen.getByRole("button", { name: /Назад/ }));
    await user.click(screen.getByRole("button", { name: /USD.*Доллары/ }));
    expect(postMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /Продолжить/ }));
    expect(postMock).not.toHaveBeenCalled();
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
    await user.click(screen.getByRole("button", { name: /Продолжить/ }));
    await user.click(screen.getByRole("button", { name: /Продолжить/ }));

    expect(screen.getByRole("option", { name: /доступно для исправления/ })).toBeEnabled();
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
    await user.click(screen.getByRole("button", { name: /Продолжить/ }));
    await user.click(screen.getByRole("button", { name: /Продолжить/ }));

    expect(screen.getByLabelText("Товар, позиция 1")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Сохранить изменения/ }));
    const body = patchMock.mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("items");
    expect(body).not.toHaveProperty("prices");
  });
});
