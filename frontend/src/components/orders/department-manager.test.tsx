import { render, screen, waitFor, within } from "@testing-library/react";
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

const configured = makeDepartment({
  order_count: 3,
  apipay_configured: true,
  apipay_webhook_configured: true,
  apipay_key_hint: "••••ab12",
  apipay_updated_at: "2026-09-15T10:00:00Z",
});

beforeEach(() => {
  mocks.me = { is_superuser: true, permissions: ["sys_permissions.manage"] };
  mocks.rows = [makeDepartment({ order_count: 3 })];
  mocks.get.mockReset();
  mocks.patch.mockReset();
  mocks.post.mockReset();
  mocks.get.mockImplementation(async () => ({ data: mocks.rows }));
  mocks.patch.mockResolvedValue({ data: {} });
});

async function openEditor(user: ReturnType<typeof userEvent.setup>, onChanged = vi.fn()) {
  render(<DepartmentManager onChanged={onChanged} />);
  await user.click(screen.getByRole("button", { name: /Отделы/ }));
  // Редактируем первый отдел из списка.
  await user.click((await screen.findAllByTitle("Изменить отдел"))[0]);
  return onChanged;
}

it("superuser sees the Kaspi block, saves key and secret without echoing them", async () => {
  const user = userEvent.setup();
  const onChanged = await openEditor(user);
  expect(screen.getByText("Kaspi / ApiPay")).toBeInTheDocument();
  expect(screen.getByText("Не подключён")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отключить Kaspi" })).not.toBeInTheDocument();
  const save = screen.getByRole("button", { name: "Сохранить ключ" });
  expect(save).toBeDisabled();

  // Ответ сервера после сохранения: ключ появился, список перечитывается и статус обновляется.
  mocks.patch.mockImplementation(async () => {
    mocks.rows = [configured];
    return { data: {} };
  });
  const keyInput = screen.getByLabelText("API-ключ");
  const secretInput = screen.getByLabelText("Секрет вебхука");
  expect(keyInput).toHaveAttribute("type", "password");
  expect(keyInput).toHaveAttribute("autocomplete", "off");
  await user.type(keyInput, " live-key-1234 ");
  expect(save).toBeEnabled();
  await user.type(secretInput, "hook");
  await user.click(save);

  await waitFor(() =>
    expect(mocks.patch).toHaveBeenCalledWith("/departments/1/", {
      apipay_api_key: "live-key-1234",
      apipay_webhook_secret: "hook",
    }),
  );
  expect(mocks.patch).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Ключ ••••ab12 · вебхук настроен")).toBeInTheDocument();
  expect(screen.getByLabelText("API-ключ")).toHaveValue("");
  expect(screen.getByLabelText("Секрет вебхука")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Сохранить ключ" })).toBeDisabled();
  expect(onChanged).toHaveBeenCalledTimes(1);
  // Ключ нигде не всплывает: ни в полях, ни текстом.
  expect(screen.queryByDisplayValue("live-key-1234")).not.toBeInTheDocument();
  expect(screen.queryByText(/live-key-1234/)).not.toBeInTheDocument();
  expect(document.body.innerHTML).not.toContain("live-key-1234");
});

it("sends only the filled field and keeps the typed value when the server rejects it", async () => {
  const user = userEvent.setup();
  await openEditor(user);
  mocks.patch.mockRejectedValueOnce(new Error("Слишком короткий ключ"));
  await user.type(screen.getByLabelText("API-ключ"), "short");
  await user.click(screen.getByRole("button", { name: "Сохранить ключ" }));
  expect(await screen.findByText("Слишком короткий ключ")).toBeInTheDocument();
  expect(mocks.patch).toHaveBeenCalledWith("/departments/1/", { apipay_api_key: "short" });
  expect(screen.getByLabelText("API-ключ")).toHaveValue("short");
});

it("shows the hint, webhook address and disconnect for a configured department", async () => {
  const user = userEvent.setup();
  mocks.rows = [configured];
  await openEditor(user);
  expect(screen.getByText(/3 заказов · доступен · Kaspi подключён/)).toBeInTheDocument();
  expect(screen.getByText("Ключ ••••ab12 · вебхук настроен")).toBeInTheDocument();
  expect(screen.getByText(`${window.location.origin}/api/webhooks/apipay/`)).toBeInTheDocument();
  expect(screen.getByText("Укажите этот адрес у ключа в кабинете ApiPay")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Скопировать" }));
  expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}/api/webhooks/apipay/`);

  mocks.patch.mockImplementation(async () => {
    mocks.rows = [makeDepartment({ order_count: 3 })];
    return { data: {} };
  });
  await user.click(screen.getByRole("button", { name: "Отключить Kaspi" }));
  await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith("/departments/1/", { apipay_api_key: "" }));
  expect(await screen.findByText("Не подключён")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отключить Kaspi" })).not.toBeInTheDocument();
  expect(screen.getByText(/3 заказов · доступен · Kaspi не подключён/)).toBeInTheDocument();
});

it("says the webhook secret is missing when only the key is set", async () => {
  const user = userEvent.setup();
  mocks.rows = [makeDepartment({ apipay_configured: true, apipay_key_hint: "••••ab12" })];
  await openEditor(user);
  expect(screen.getByText("Ключ ••••ab12 · без секрета вебхука")).toBeInTheDocument();
});

it("shows the Kaspi status in the row but no block to a manager who is not a superuser", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["sys_permissions.manage"] };
  mocks.rows = [configured, makeDepartment({ id: 2, code: "city", name: "Нью-Сити", is_default: false })];
  await openEditor(user);
  const dialog = within(screen.getByRole("dialog"));
  expect(dialog.getByText(/Kaspi подключён/)).toBeInTheDocument();
  expect(dialog.getByText(/Kaspi не подключён/)).toBeInTheDocument();
  expect(screen.queryByText("Kaspi / ApiPay")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("API-ключ")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сохранить ключ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отключить Kaspi" })).not.toBeInTheDocument();
  // Обычное редактирование осталось.
  expect(screen.getByRole("button", { name: "Сохранить изменения" })).toBeInTheDocument();
});

it("hides the block for a new department even for a superuser", async () => {
  const user = userEvent.setup();
  render(<DepartmentManager onChanged={() => {}} />);
  await user.click(screen.getByRole("button", { name: /Отделы/ }));
  expect(await screen.findByText("Новый отдел")).toBeInTheDocument();
  expect(screen.queryByText("Kaspi / ApiPay")).not.toBeInTheDocument();
  await user.click(screen.getByTitle("Изменить отдел"));
  expect(screen.getByText("Kaspi / ApiPay")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Сбросить" }));
  expect(screen.queryByText("Kaspi / ApiPay")).not.toBeInTheDocument();
});
