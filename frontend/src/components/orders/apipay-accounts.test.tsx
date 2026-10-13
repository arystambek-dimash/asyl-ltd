import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { makeDepartment } from "@/test-utils/factories";
import type { ApiPayAccount } from "@/lib/types";
import { ApiPayAccounts } from "./apipay-accounts";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  accounts: [] as unknown[],
}));
vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mocks.get(...args),
    patch: (...args: unknown[]) => mocks.patch(...args),
    post: (...args: unknown[]) => mocks.post(...args),
    delete: (...args: unknown[]) => mocks.delete(...args),
  },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

const account: ApiPayAccount = {
  id: 7,
  name: "Kaspi Мельница",
  key_hint: "••••ab12",
  webhook_configured: true,
  updated_at: "2026-10-13T10:00:00Z",
  departments: [1],
};
const mill = makeDepartment({ apipay_configured: true, apipay_account: 7, apipay_account_name: account.name });
const city = makeDepartment({ id: 2, code: "city", name: "Нью-Сити", is_default: false });

beforeEach(() => {
  mocks.accounts = [account];
  for (const fn of [mocks.get, mocks.patch, mocks.post, mocks.delete]) fn.mockReset();
  mocks.get.mockImplementation(async () => ({ data: mocks.accounts }));
  mocks.patch.mockResolvedValue({ data: {} });
  mocks.post.mockResolvedValue({ data: {} });
  mocks.delete.mockResolvedValue({ data: {} });
});

function renderTab(onChanged = vi.fn(async () => {})) {
  render(<ApiPayAccounts departments={[mill, city]} onChanged={onChanged} />);
  return onChanged;
}

it("connects departments without Kaspi to the only account in one click", async () => {
  const user = userEvent.setup();
  const onChanged = renderTab();

  expect(await screen.findByText(/Без Kaspi: Нью-Сити/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Подключить к «Kaspi Мельница»" }));

  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/apipay-accounts/7/", { departments: [1, 2] }));
  expect(onChanged).toHaveBeenCalled();
});

it("shows the key hint and webhook state and toggles a department chip", async () => {
  const user = userEvent.setup();
  renderTab();

  const card = (await screen.findByText("Kaspi Мельница")).closest("section")!;
  expect(within(card).getByText("••••ab12")).toBeInTheDocument();
  expect(within(card).getByText("Вебхук настроен")).toBeInTheDocument();
  expect(within(card).getByRole("button", { name: /Мельница/, pressed: true })).toBeInTheDocument();

  await user.click(within(card).getByRole("button", { name: /Нью-Сити/, pressed: false }));
  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/apipay-accounts/7/", { departments: [1, 2] }));
});

it("creates a connection for the departments without Kaspi and never echoes the key", async () => {
  const user = userEvent.setup();
  mocks.accounts = [];
  renderTab();

  await user.click(await screen.findByRole("button", { name: "Подключить ApiPay" }));
  const form = screen.getByText("Новое подключение ApiPay").closest("form")!;
  const submit = within(form).getByRole("button", { name: "Подключить" });
  // Отдел без Kaspi отмечен заранее, отдел с другим подключением — нет.
  expect(within(form).getByRole("button", { name: /Нью-Сити/, pressed: true })).toBeInTheDocument();
  expect(within(form).getByRole("button", { name: /Мельница/, pressed: false })).toBeInTheDocument();
  expect(submit).toBeDisabled();

  await user.type(within(form).getByPlaceholderText("Например, Kaspi Мельница"), "Kaspi Сити");
  expect(submit).toBeDisabled();
  const key = within(form).getByLabelText("API-ключ");
  expect(key).toHaveAttribute("type", "password");
  await user.type(key, " live-key-1234 ");
  await user.type(within(form).getByLabelText("Секрет вебхука"), "hook");
  await user.click(submit);

  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/apipay-accounts/", {
      name: "Kaspi Сити",
      api_key: "live-key-1234",
      webhook_secret: "hook",
      departments: [2],
    }),
  );
  await waitFor(() => expect(screen.queryByText("Новое подключение ApiPay")).not.toBeInTheDocument());
  expect(document.body.innerHTML).not.toContain("live-key-1234");
});

it("edits only the filled fields and keeps the form open on a server error", async () => {
  const user = userEvent.setup();
  renderTab();

  await user.click(await screen.findByRole("button", { name: "Изменить Kaspi Мельница" }));
  const form = screen.getByText("Изменить «Kaspi Мельница»").closest("form")!;
  mocks.patch.mockRejectedValueOnce(new Error("Этот ключ уже подключён как «Другой»"));
  await user.type(within(form).getByLabelText("API-ключ"), "dup-key-1234");
  await user.click(within(form).getByRole("button", { name: "Сохранить" }));

  expect(await within(form).findByText("Этот ключ уже подключён как «Другой»")).toBeInTheDocument();
  expect(mocks.patch).toHaveBeenCalledWith("/apipay-accounts/7/", {
    name: "Kaspi Мельница",
    api_key: "dup-key-1234",
    departments: [1],
  });
});

it("warns which departments lose Kaspi before deleting a connection", async () => {
  const user = userEvent.setup();
  renderTab();

  await user.click(await screen.findByRole("button", { name: "Удалить Kaspi Мельница" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/Мельница останутся без Kaspi/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Удалить" }));

  await waitFor(() => expect(mocks.delete).toHaveBeenCalledWith("/apipay-accounts/7/"));
});
