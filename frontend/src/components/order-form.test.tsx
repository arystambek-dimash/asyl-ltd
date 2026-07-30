import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OrderForm } from "@/components/order-form";
import type { Client, Department, Order, Product } from "@/lib/types";

const useApiMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { sales_department: null } }),
}));
vi.mock("@/lib/api", () => ({
  api: { patch: vi.fn(), post: vi.fn() },
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
});
