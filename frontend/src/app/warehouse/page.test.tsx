import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me, Product, StockItem, Warehouse } from "@/lib/types";
import WarehousePage from "./page";

const navigation = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
const useApiMock = vi.hoisted(() => vi.fn());
const apiMocks = vi.hoisted(() => ({ post: vi.fn(), patch: vi.fn(), delete: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.search),
}));
vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("@/lib/api", () => ({
  api: apiMocks,
  apiError: () => "Ошибка сохранения",
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) => (
    <main>
      <h1>{title}</h1>
      {actions}
      {children}
    </main>
  ),
}));

const me: Me = {
  id: 1,
  username: "warehouse-admin",
  first_name: "",
  last_name: "",
  is_client: false,
  is_superuser: false,
  is_monoblock: false,
  monoblock_name: null,
  monoblock_camera: null,
  permissions: ["warehouse.view", "warehouse.adjust", "catalog.view"],
  position: null,
  client_id: null,
  sales_department: null,
};

vi.mock("@/store/auth", () => ({ useAuth: () => ({ me }) }));

const warehouses: Warehouse[] = [
  {
    id: 1,
    code: "reserve",
    name: "Резервный склад",
    address: "Корпус 2",
    is_active: true,
    is_default: false,
  },
  {
    id: 2,
    code: "main",
    name: "Основной склад",
    address: "Корпус 1",
    is_active: true,
    is_default: true,
  },
];

const products: Product[] = [
  {
    id: 10,
    name: "Красная мука",
    weight_kg: "50.00",
    is_active: true,
    label: "Красная мука · 50 кг",
  },
  {
    id: 20,
    name: "Синяя мука",
    weight_kg: "50.00",
    is_active: true,
    label: "Синяя мука · 50 кг",
  },
];

const assignedStock: StockItem = {
  id: 100,
  warehouse: 1,
  warehouse_name: "Резервный склад",
  product: 10,
  product_label: "Красная мука · 50 кг",
  grade: "Красная мука",
  color: "Red",
  color_label: "Красный",
  packaging: "50 кг",
  weight_kg: "50.00",
  bags: 15,
};

function apiState<T>(
  data: T,
  overrides: Partial<{ loading: boolean; error: string; errorStatus: number | null }> = {},
) {
  return {
    data,
    loading: false,
    error: "",
    errorStatus: null,
    reload: vi.fn(async () => undefined),
    setData: vi.fn(),
    ...overrides,
  };
}

describe("WarehousePage multi-warehouse inventory", () => {
  beforeEach(() => {
    navigation.search = "";
    navigation.replace.mockReset();
    navigation.replace.mockImplementation((url: string) => {
      navigation.search = url.split("?")[1] ?? "";
    });
    useApiMock.mockReset();
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/warehouses/") return apiState(warehouses);
      if (url === "/products/") return apiState(products);
      if (url === "/stock/") return apiState([assignedStock]);
      if (url === "/stock/?warehouse=1") return apiState([assignedStock]);
      if (url === "/stock/?warehouse=2") return apiState([]);
      return apiState(null);
    });
    apiMocks.post.mockReset();
    apiMocks.patch.mockReset();
    apiMocks.delete.mockReset();
    apiMocks.post.mockResolvedValue({ data: {} });
    apiMocks.patch.mockResolvedValue({ data: {} });
    apiMocks.delete.mockResolvedValue({ data: {} });
  });

  it("opens the default warehouse, stores it in the URL and requests only its stock", async () => {
    render(<WarehousePage />);

    expect(screen.getByRole("heading", { name: "Склады" })).toBeInTheDocument();
    expect(screen.getByLabelText("Склад")).toHaveValue("2");
    expect(screen.queryByRole("group", { name: "Быстрый выбор склада" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Открыть склад/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Корпус 1")).not.toBeInTheDocument();
    expect(screen.queryByText("Корпус 2")).not.toBeInTheDocument();
    expect(useApiMock).toHaveBeenCalledWith("/stock/?warehouse=2");
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/warehouse?warehouse=2", { scroll: false }));
  });

  it("switches the warehouse through the URL and reloads the scoped stock", async () => {
    navigation.search = "warehouse=1";
    const user = userEvent.setup();
    const view = render(<WarehousePage />);

    await user.selectOptions(screen.getByLabelText("Склад"), "2");
    expect(navigation.replace).toHaveBeenCalledWith("/warehouse?warehouse=2", { scroll: false });

    view.rerender(<WarehousePage />);
    expect(screen.getByLabelText("Склад")).toHaveValue("2");
    expect(useApiMock).toHaveBeenCalledWith("/stock/?warehouse=2");
  });

  it("allows a product stored elsewhere to be added to the selected warehouse", async () => {
    navigation.search = "warehouse=2";
    const user = userEvent.setup();
    render(<WarehousePage />);

    await user.click(screen.getByRole("button", { name: "Добавить товар на склад Основной склад" }));
    const productSelect = await screen.findByLabelText("Товар");
    expect(screen.getByRole("option", { name: "Красная мука · 50 кг" })).toBeInTheDocument();
    await user.selectOptions(productSelect, "10");
    await user.type(screen.getByLabelText("Количество мешков"), "25");
    await user.click(screen.getByRole("button", { name: "Добавить 25 меш." }));

    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith("/stock/adjust/", {
        warehouse: 2,
        product: 10,
        delta: 25,
      }),
    );
  });

  it("edits a warehouse through the management dialog", async () => {
    navigation.search = "warehouse=2";
    const user = userEvent.setup();
    apiMocks.patch.mockResolvedValue({ data: { ...warehouses[0], name: "Резерв" } });
    render(<WarehousePage />);

    await user.click(screen.getByRole("button", { name: "Управление" }));
    await user.click(await screen.findByRole("button", { name: "Резервный склад" }));
    expect(screen.queryByLabelText("Код")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Адрес")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Активный склад")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Основной склад")).not.toBeInTheDocument();
    const nameInput = screen.getByLabelText("Название");
    await user.clear(nameInput);
    await user.type(nameInput, "Резерв");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(apiMocks.patch).toHaveBeenCalledWith("/warehouses/1/", {
        name: "Резерв",
      }),
    );
  });

  it("creates a warehouse through the management dialog", async () => {
    navigation.search = "warehouse=2";
    const user = userEvent.setup();
    const created: Warehouse = {
      id: 3,
      code: "north",
      name: "Северный склад",
      address: "Северная зона",
      is_active: true,
      is_default: false,
    };
    apiMocks.post.mockResolvedValue({ data: created });
    render(<WarehousePage />);

    await user.click(screen.getByRole("button", { name: "Управление" }));
    await user.type(await screen.findByLabelText("Название"), "Северный склад");
    await user.click(screen.getByRole("button", { name: "Создать склад" }));

    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith("/warehouses/", {
        name: "Северный склад",
      }),
    );
  });

  it("moves stock to another warehouse and previews both balances", async () => {
    navigation.search = "warehouse=1";
    const destinationStock: StockItem = {
      ...assignedStock,
      id: 101,
      warehouse: 2,
      warehouse_name: "Основной склад",
      bags: 4,
    };
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/warehouses/") return apiState(warehouses);
      if (url === "/products/") return apiState(products);
      if (url === "/stock/") return apiState([assignedStock, destinationStock]);
      if (url === "/stock/?warehouse=1") return apiState([assignedStock]);
      return apiState([]);
    });
    const user = userEvent.setup();
    render(<WarehousePage />);

    await user.click(screen.getAllByRole("button", { name: "Действия с товаром" })[0]);
    await user.click(screen.getByRole("menuitem", { name: "Изменить" }));
    await user.click(screen.getByRole("button", { name: /Перемещение/ }));
    await user.selectOptions(screen.getByLabelText("Склад назначения"), "2");
    await user.type(screen.getByLabelText("Количество мешков"), "10");

    expect(screen.getByText("Откуда · Резервный склад")).toBeInTheDocument();
    expect(screen.getByText("Куда · Основной склад")).toBeInTheDocument();
    expect(screen.getByText("10 меш. будут перенесены без изменения общего остатка.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Переместить 10 меш." }));
    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith("/stock/transfer/", {
        from_warehouse: 1,
        to_warehouse: 2,
        product: 10,
        bags: 10,
      }),
    );
  });

  it("falls back to the legacy stock API when warehouses are not deployed yet", async () => {
    useApiMock.mockImplementation((url: string | null) => {
      if (url === "/warehouses/") {
        return apiState(null, { error: "Страница не найдена", errorStatus: 404 });
      }
      if (url === "/products/") return apiState(products);
      if (url === "/stock/") return apiState([assignedStock]);
      return apiState(null);
    });
    const user = userEvent.setup();

    render(<WarehousePage />);

    expect(screen.getByLabelText("Склад")).toHaveValue("0");
    expect(screen.getByRole("option", { name: "Основной склад · основной" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Управление" })).not.toBeInTheDocument();
    expect(useApiMock).toHaveBeenCalledWith("/stock/");

    await user.click(screen.getByRole("button", { name: "Добавить товар на склад Основной склад" }));
    await user.selectOptions(await screen.findByLabelText("Товар"), "20");
    await user.type(screen.getByLabelText("Количество мешков"), "10");
    await user.click(screen.getByRole("button", { name: "Добавить 10 меш." }));

    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith("/stock/adjust/", {
        product: 20,
        delta: 10,
      }),
    );
  });
});
