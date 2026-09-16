import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RegisterPage from "./page";

const mocks = vi.hoisted(() => ({
  adoptSession: vi.fn(),
  registerClient: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ adoptSession: mocks.adoptSession }),
}));

vi.mock("@/lib/portal-actions", () => ({
  registerClient: mocks.registerClient,
}));

async function fillRequired(user: ReturnType<typeof userEvent.setup>, phone: string) {
  await user.type(screen.getByLabelText("Имя"), "Бахредин");
  await user.type(screen.getByLabelText("Телефон"), phone);
  await user.type(screen.getByLabelText("Логин"), "baha@gmail.com");
  await user.type(screen.getByLabelText("Пароль"), "strong-password");
}

describe("RegisterPage", () => {
  beforeEach(() => {
    mocks.adoptSession.mockReset();
    mocks.registerClient.mockReset();
    mocks.replace.mockReset();
    mocks.registerClient.mockResolvedValue({ access: "access-token", refresh: "refresh-token" });
    mocks.adoptSession.mockResolvedValue(undefined);
  });

  it("прячет необязательные реквизиты под кнопку", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    expect(screen.queryByLabelText(/Название ТОО \/ ИП/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Добавить ТОО \/ ИП/ }));

    expect(screen.getByLabelText(/Название ТОО \/ ИП/)).not.toBeRequired();
    expect(screen.getByLabelText(/ИИН\/БИН/)).not.toBeRequired();
  });

  it("регистрирует клиента с номером по маске и пустыми реквизитами", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await fillRequired(user, "87055656565");
    expect(screen.getByLabelText("Телефон")).toHaveValue("(705) 565-65-65");
    await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));

    await waitFor(() =>
      expect(mocks.registerClient).toHaveBeenCalledWith({
        username: "baha@gmail.com",
        password: "strong-password",
        first_name: "Бахредин",
        last_name: "",
        company_name: "",
        phone: "+7 (705) 565-65-65",
        country: "Казахстан",
        iin: "",
      }),
    );
    expect(mocks.adoptSession).toHaveBeenCalledWith("access-token", "refresh-token");
    expect(mocks.replace).toHaveBeenCalledWith("/portal/catalog");
  });

  it("переключает страну по вставленному номеру с кодом", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.click(screen.getByLabelText("Телефон"));
    await user.paste("+998 90 123 45 67");

    expect(screen.getByLabelText("Страна телефона")).toHaveValue("Узбекистан");
    expect(screen.getByLabelText("Телефон")).toHaveValue("90 123-45-67");
  });

  it("не отправляет неполный номер и говорит, сколько цифр не хватает", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await fillRequired(user, "70556");
    await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));

    expect(await screen.findByText("Номер неполный: ещё 5 цифр")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Телефон")).toHaveFocus());
    expect(mocks.registerClient).not.toHaveBeenCalled();
  });

  it("показывает ошибку сервера под нужным полем", async () => {
    const user = userEvent.setup();
    mocks.registerClient.mockRejectedValue({
      response: { status: 400, data: { detail: { username: ["Это имя пользователя уже занято"] }, code: "invalid" } },
    });
    render(<RegisterPage />);

    await fillRequired(user, "7055656565");
    await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));

    expect(await screen.findByText("Это имя пользователя уже занято")).toBeInTheDocument();
    expect(screen.getByLabelText("Логин")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
