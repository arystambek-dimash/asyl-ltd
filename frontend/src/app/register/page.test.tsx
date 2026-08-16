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

describe("RegisterPage", () => {
  beforeEach(() => {
    mocks.adoptSession.mockReset();
    mocks.registerClient.mockReset();
    mocks.replace.mockReset();
    mocks.registerClient.mockResolvedValue({ access: "access-token", refresh: "refresh-token" });
    mocks.adoptSession.mockResolvedValue(undefined);
  });

  it("помечает название компании и ИИН/БИН как необязательные", () => {
    render(<RegisterPage />);

    expect(screen.getByLabelText(/Название ТОО \/ ИП/)).not.toBeRequired();
    expect(screen.getByLabelText(/ИИН\/БИН/)).not.toBeRequired();
  });

  it("регистрирует клиента с пустыми реквизитами", async () => {
    const user = userEvent.setup();
    render(<RegisterPage />);

    await user.type(screen.getByLabelText("Имя"), "Бахредин");
    await user.type(screen.getByLabelText("Телефон"), "87055656565");
    await user.type(screen.getByLabelText("Логин"), "baha@gmail.com");
    await user.type(screen.getByLabelText("Пароль"), "strong-password");
    await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));

    await waitFor(() =>
      expect(mocks.registerClient).toHaveBeenCalledWith({
        username: "baha@gmail.com",
        password: "strong-password",
        first_name: "Бахредин",
        last_name: "",
        company_name: "",
        phone: "87055656565",
        iin: "",
      }),
    );
    expect(mocks.adoptSession).toHaveBeenCalledWith("access-token", "refresh-token");
    expect(mocks.replace).toHaveBeenCalledWith("/portal/catalog");
  });
});
