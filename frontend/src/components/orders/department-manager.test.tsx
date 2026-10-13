import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { makeDepartment } from "@/test-utils/factories";
import { DepartmentManager } from "./department-manager";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  me: { is_superuser: true, permissions: ["sys_permissions.manage"] },
  rows: [] as unknown[],
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mocks.get(...args),
    patch: (...args: unknown[]) => mocks.patch(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

const mill = makeDepartment({
  order_count: 3,
  apipay_configured: true,
  apipay_account: 7,
  apipay_account_name: "Kaspi Мельница",
});
const city = makeDepartment({ id: 2, code: "city", name: "Нью-Сити", is_default: false });

beforeEach(() => {
  mocks.me = { is_superuser: true, permissions: ["sys_permissions.manage"] };
  mocks.rows = [mill, city];
  mocks.get.mockReset();
  mocks.patch.mockReset();
  mocks.post.mockReset();
  mocks.get.mockImplementation(async (url: string) => ({
    data: url.startsWith("/apipay-accounts/") ? [] : mocks.rows,
  }));
  mocks.patch.mockResolvedValue({ data: {} });
});

async function openManager(user: ReturnType<typeof userEvent.setup>) {
  render(<DepartmentManager onChanged={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: /Отделы/ }));
  return within(await screen.findByRole("dialog"));
}

it("shows each department's Kaspi connection and leads the superuser to the Kaspi tab", async () => {
  const user = userEvent.setup();
  const dialog = await openManager(user);

  expect(await dialog.findByText("Kaspi: Kaspi Мельница")).toBeInTheDocument();
  expect(dialog.getByText("без Kaspi")).toBeInTheDocument();
  // Счётчик на вкладке — сколько активных отделов без Kaspi.
  expect(dialog.getByRole("tab", { name: "Kaspi · ApiPay, 1" })).toBeInTheDocument();

  await user.click(dialog.getByRole("button", { name: "без Kaspi" }));
  expect(dialog.getByRole("tab", { name: "Kaspi · ApiPay, 1" })).toHaveAttribute("aria-selected", "true");
  expect(await dialog.findByText(/Без Kaspi: Нью-Сити/)).toBeInTheDocument();
  expect(mocks.get).toHaveBeenCalledWith("/apipay-accounts/", expect.anything());
});

it("shows the Kaspi status but no Kaspi tab to a manager who is not a superuser", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["sys_permissions.manage"] };
  const dialog = await openManager(user);

  expect(await dialog.findByText("Kaspi: Kaspi Мельница")).toBeInTheDocument();
  expect(dialog.getByText("без Kaspi")).toBeInTheDocument();
  expect(dialog.queryByRole("tab")).not.toBeInTheDocument();
  expect(dialog.queryByRole("button", { name: "без Kaspi" })).not.toBeInTheDocument();
  expect(mocks.get).not.toHaveBeenCalledWith("/apipay-accounts/", expect.anything());

  // Обычное редактирование отдела осталось.
  await user.click(dialog.getAllByTitle("Изменить отдел")[0]);
  expect(dialog.getByRole("button", { name: "Сохранить изменения" })).toBeInTheDocument();
});
