import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Product, Warehouse } from "@/lib/types";

import ProductsPage from "./page";

const ALL_PERMISSIONS = ["catalog.view", "catalog.create", "catalog.edit", "warehouse.adjust"];

const mocks = vi.hoisted(() => ({
  permissions: [] as string[],
  data: new Map<string, unknown>(),
  post: vi.fn(),
  patch: vi.fn(),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, is_superuser: false, permissions: mocks.permissions }, loading: false }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => ({
    data: url ? (mocks.data.get(url) ?? null) : null,
    error: "",
    loading: false,
    reload: vi.fn(),
    setData: vi.fn(),
  }),
}));
vi.mock("@/lib/api", () => ({
  api: { post: mocks.post, patch: mocks.patch },
  apiError: (error: Error) => error.message,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, actions }: { children: ReactNode; actions?: ReactNode }) => (
    <main>
      {actions}
      {children}
    </main>
  ),
}));

const warehouse: Warehouse = {
  id: 4,
  code: "main",
  name: "Основной",
  address: "",
  is_active: true,
  is_default: true,
};

const created = {
  id: 9,
  name: "Высший сорт",
  label: "Высший сорт 50 кг",
  color: "Red",
  weight_kg: "50",
  aliases: [],
} as unknown as Product;

describe("ProductsPage — новый товар", () => {
  beforeEach(() => {
    mocks.permissions = ALL_PERMISSIONS;
    mocks.post.mockReset();
    mocks.patch.mockReset();
    mocks.data = new Map<string, unknown>([
      ["/products/", []],
      ["/products/?archived=1", []],
      ["/warehouses/", [warehouse]],
    ]);
  });

  it("posts the stock right after creation and, when it fails, retries only the stock on the created product", async () => {
    let stockAttempts = 0;
    mocks.post.mockImplementation(async (url: string) => {
      if (url === "/products/") return { data: created };
      if (url === "/stock/adjust/" && ++stockAttempts === 1) throw new Error("Склад недоступен");
      return { data: {} };
    });
    mocks.patch.mockResolvedValue({ data: created });
    const user = userEvent.setup();
    render(<ProductsPage />);

    await user.click(screen.getByRole("button", { name: "Создать товар" }));
    const dialog = screen.getByRole("dialog");
    // У нового товара ещё нет кодов в отчётах — редактор появится, когда товар сохранён.
    expect(within(dialog).queryByLabelText("Коды в отчётах о вагонах")).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Название"), "Высший сорт");
    await user.type(within(dialog).getByLabelText("Количество мешков"), "100");
    await user.click(within(dialog).getByRole("button", { name: "Создать" }));

    expect(await within(dialog).findByText("Склад недоступен")).toBeInTheDocument();
    // Товар создан: окно правит его — коды в отчётах уже доступны, приход остаётся к повтору.
    expect(within(dialog).getByLabelText("Коды в отчётах о вагонах")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Количество мешков")).toHaveValue(100);

    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mocks.post.mock.calls.filter(([url]) => url === "/products/")).toHaveLength(1);
    expect(mocks.patch).toHaveBeenCalledWith("/products/9/", expect.objectContaining({ name: "Высший сорт" }));
    expect(mocks.post).toHaveBeenLastCalledWith("/stock/adjust/", { warehouse: 4, product: 9, delta: 100 });
  });

  it("hides the stock step without warehouse.adjust", async () => {
    mocks.permissions = ["catalog.view", "catalog.create", "catalog.edit"];
    const user = userEvent.setup();
    render(<ProductsPage />);
    await user.click(screen.getByRole("button", { name: "Создать товар" }));
    expect(screen.queryByText("Сколько добавить на склад?")).not.toBeInTheDocument();
  });
});
