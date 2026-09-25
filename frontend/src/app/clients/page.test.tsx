import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import ClientsPage from "./page";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), permissions: ["clients.view"] }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { permissions: mocks.permissions }, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: (e: unknown) => (e as Error).message,
  isCanceledRequest: () => false,
}));

beforeEach(() => {
  mocks.permissions = ["clients.view"];
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/" && !url.searchParams.has("department")) {
      throw new Error("Клиенты не загрузились");
    }
    if (url.searchParams.has("page")) return { data: { results: [], count: 0, next: null, previous: null } };
    return { data: [] };
  });
});

it("shows a load error of the default paged list instead of «Здесь пусто»", async () => {
  render(<ClientsPage />);
  const alert = await screen.findByText("Клиенты не загрузились");
  expect(alert.closest("[role=alert]")).toBeInTheDocument();
});

it("пока отделы грузятся, показывает текущий отдел клиента без приписки «(архивный)»", async () => {
  mocks.permissions = ["clients.view", "clients.edit"];
  const client = {
    id: 7,
    username: "client7",
    first_name: "Асель",
    last_name: "Нурланова",
    phone: "+77010000007",
    name: "Асель Нурланова",
    company_name: "",
    country: "",
    currency: "KZT",
    iin: "",
    bank: "",
    bank_account: "",
    department: 3,
    department_name: "Оптовый отдел",
    user: 70,
    portal_access_enabled: false,
  };
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    // Список отделов формы ещё не пришёл.
    if (url.pathname === "/departments/" && url.searchParams.get("all") === "1") return new Promise(() => undefined);
    if (url.pathname === "/clients/" && url.searchParams.has("page"))
      return { data: { results: [client], count: 1, next: null, previous: null } };
    return { data: [] };
  });
  const user = userEvent.setup();
  render(<ClientsPage />);

  const [menu] = await screen.findAllByRole("button", { name: "Действия" });
  await user.click(menu);
  await user.click(screen.getByRole("menuitem", { name: "Изменить" }));

  const dialog = await screen.findByRole("dialog");
  const trigger = within(dialog)
    .getAllByRole("combobox")
    .find((element) => element.textContent?.includes("Оптовый отдел"));
  expect(trigger).toBeDefined();
  expect(trigger).not.toHaveTextContent("архивный");
});

it("форма клиента не отправляется с ошибками полей, а после исправления шлёт очищенные данные", async () => {
  mocks.permissions = ["clients.view", "clients.create"];
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.searchParams.has("page")) return { data: { results: [], count: 0, next: null, previous: null } };
    return { data: [] };
  });
  const user = userEvent.setup();
  render(<ClientsPage />);

  await user.click(await screen.findByRole("button", { name: "Добавить клиента" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Имя"), "А");
  await user.type(within(dialog).getByLabelText("ИИН / БИН"), "123");
  await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

  expect(within(dialog).getByText("Введите имя (мин. 2 символа)")).toBeInTheDocument();
  expect(within(dialog).getByText("Введите номер полностью")).toBeInTheDocument();
  expect(within(dialog).getByText("ИИН/БИН — 12 цифр")).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Имя")).toHaveAttribute("aria-invalid", "true");
  expect(mocks.post).not.toHaveBeenCalled();

  await user.type(within(dialog).getByLabelText("Имя"), "сель");
  expect(within(dialog).queryByText("Введите имя (мин. 2 символа)")).not.toBeInTheDocument();
  await user.type(within(dialog).getByLabelText(/Фамилия/), "  Нурланова  ");
  await user.type(within(dialog).getByLabelText("Номер телефона"), "7010000007");
  await user.type(within(dialog).getByLabelText("ИИН / БИН"), "456789012");
  await user.type(within(dialog).getByLabelText("Расчётный счёт (IBAN)"), "kz12");
  await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

  expect(mocks.post).toHaveBeenCalledWith(
    "/clients/",
    expect.objectContaining({
      first_name: "Асель",
      last_name: "Нурланова",
      phone: "+7 (701) 000-00-07",
      iin: "123456789012",
      bank_account: "KZ12",
      currency: "KZT",
      department: null,
    }),
  );
});
