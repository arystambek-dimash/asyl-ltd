import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Product, StockItem, Warehouse } from "@/lib/types";
import { apiState } from "@/test-utils/api";
import { makeMe } from "@/test-utils/factories";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";
import WarehousePage from "./page";

const useApiMock = vi.hoisted(() => vi.fn());
const apiMocks = vi.hoisted(() => ({ post: vi.fn(), patch: vi.fn(), delete: vi.fn() }));

vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("@/lib/api", () => ({
  api: apiMocks,
  apiError: () => "Ошибка сохранения",
}));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));

const me = makeMe({
  username: "warehouse-admin",
  first_name: "",
  last_name: "",
  permissions: ["warehouse.view", "warehouse.adjust", "catalog.view"],
});

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

/** useApi по адресу: склады, каталог и остатки двух складов; прочие адреса — пусто. */
function routeApi({
  catalog = products,
  stock1 = [assignedStock],
  stock2 = [],
}: { catalog?: Product[]; stock1?: StockItem[]; stock2?: StockItem[] } = {}) {
  useApiMock.mockImplementation((url: string | null) => {
    if (url === "/warehouses/") return apiState(warehouses);
    if (url === "/products/") return apiState(catalog);
    if (url === "/stock/?warehouse=1") return apiState(stock1);
    if (url === "/stock/?warehouse=2") return apiState(stock2);
    return apiState(null);
  });
}

describe("WarehousePage multi-warehouse inventory", () => {
  beforeEach(() => {
    resetNavigation("/warehouse");
    useApiMock.mockReset();
    routeApi();
    me.permissions = ["warehouse.view", "warehouse.adjust", "catalog.view"];
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
    await waitFor(() => expect(routerCalls.replace).toEqual(["/warehouse?warehouse=2"]));
    expect(routerCalls.options).toEqual([{ scroll: false }]);
  });

  it("switches the warehouse through the URL and reloads the scoped stock", async () => {
    resetNavigation("/warehouse?warehouse=1");
    const user = userEvent.setup();
    render(<WarehousePage />);

    await user.selectOptions(screen.getByLabelText("Склад"), "2");
    expect(routerCalls.replace).toEqual(["/warehouse?warehouse=2"]);
    expect(routerCalls.options).toEqual([{ scroll: false }]);
    expect(screen.getByLabelText("Склад")).toHaveValue("2");
    expect(useApiMock).toHaveBeenCalledWith("/stock/?warehouse=2");
  });

  it("allows a product stored elsewhere to be added to the selected warehouse", async () => {
    resetNavigation("/warehouse?warehouse=2");
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

  it("creates a new product right from the warehouse when the whole catalog is already there", async () => {
    resetNavigation("/warehouse?warehouse=1");
    me.permissions = ["warehouse.view", "warehouse.adjust", "catalog.create"];
    routeApi({ catalog: [products[0]] });
    apiMocks.post.mockImplementation(async (url: string) => ({ data: url === "/products/" ? { id: 30 } : {} }));
    const user = userEvent.setup();
    render(<WarehousePage />);

    const add = screen.getByRole("button", { name: "Добавить товар на склад Резервный склад" });
    expect(add).toBeEnabled();
    await user.click(add);

    expect(await screen.findByLabelText("Товар")).toHaveValue("__new");
    const dialog = within(screen.getByRole("dialog"));
    await user.type(dialog.getByLabelText("Название"), "Первый сорт");
    await user.selectOptions(dialog.getByLabelText("Цвет (тип)"), "Blue");
    await user.selectOptions(dialog.getByLabelText("Фасовка"), "25");
    await user.type(dialog.getByLabelText("Количество мешков"), "30");
    await user.click(dialog.getByRole("button", { name: "Добавить 30 меш." }));

    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith("/stock/adjust/", { warehouse: 1, product: 30, delta: 30 }),
    );
    expect(apiMocks.post).toHaveBeenCalledWith("/products/", { name: "Первый сорт", weight_kg: "25", color: "Blue" });
  });

  it("explains why a warehouse keeper without catalog rights cannot add a new product", async () => {
    resetNavigation("/warehouse?warehouse=1");
    me.permissions = ["warehouse.view", "warehouse.adjust"];
    routeApi({ catalog: [products[0]] });
    const user = userEvent.setup();
    render(<WarehousePage />);

    await user.click(screen.getByRole("button", { name: "Добавить товар на склад Резервный склад" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Все товары каталога уже есть на этом складе");
    expect(screen.queryByRole("option", { name: "+ Новый товар…" })).not.toBeInTheDocument();
  });

  it("edits a warehouse through the management dialog", async () => {
    resetNavigation("/warehouse?warehouse=2");
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
    resetNavigation("/warehouse?warehouse=2");
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
    resetNavigation("/warehouse?warehouse=1");
    const destinationStock: StockItem = {
      ...assignedStock,
      id: 101,
      warehouse: 2,
      warehouse_name: "Основной склад",
      bags: 4,
    };
    routeApi({ stock2: [destinationStock] });
    const user = userEvent.setup();
    render(<WarehousePage />);

    await user.click(screen.getAllByRole("button", { name: "Изменить" })[0]);
    await user.click(screen.getByRole("button", { name: /Перемещение/ }));
    await user.selectOptions(screen.getByLabelText("Склад назначения"), "2");
    await user.type(screen.getByLabelText("Количество мешков"), "10");

    expect(screen.getByText("Откуда · Резервный склад")).toBeInTheDocument();
    expect(screen.getByText("Куда · Основной склад")).toBeInTheDocument();
    // Превью склада назначения: 4 меш. сейчас, 14 после перемещения.
    expect(screen.getByText((_, element) => element?.textContent === "4 → 14 меш.")).toBeInTheDocument();
    expect(useApiMock).not.toHaveBeenCalledWith("/stock/");
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
});
