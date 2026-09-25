import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/lib/types";
import { makeMe } from "@/test-utils/factories";
import { useAuth } from "@/store/auth";

import LoginPage from "./page";

const mocks = vi.hoisted(() => {
  const replace = vi.fn();
  return {
    completeInitialPasswordChange: vi.fn(),
    loadMe: vi.fn(),
    login: vi.fn(),
    replace,
    // Как в Next.js, роутер стабилен между рендерами.
    router: { replace },
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("next/image", () => import("@/test-utils/next-image"));

vi.mock("@/store/auth", async () => {
  const { create } = await import("zustand");
  return {
    useAuth: create(() => ({
      completeInitialPasswordChange: mocks.completeInitialPasswordChange,
      loadMe: mocks.loadMe,
      login: mocks.login,
      me: null as Me | null,
    })),
  };
});

const passwordChangeRequired = {
  response: {
    status: 401,
    data: {
      detail: "Смените временный пароль.",
      code: "password_change_required",
    },
  },
};

const client = makeMe({
  id: 7,
  username: "client-7",
  first_name: "Алия",
  last_name: "Серикова",
  is_client: true,
});

async function requestInitialPasswordChange() {
  const user = userEvent.setup();
  render(<LoginPage />);
  await user.type(screen.getByLabelText("Логин"), "client-7");
  await user.type(screen.getByLabelText("Пароль"), "temporary-password");
  await user.click(screen.getByRole("button", { name: "Войти" }));
  await screen.findByLabelText("Новый пароль");
  return user;
}

describe("LoginPage initial password change", () => {
  beforeEach(() => {
    mocks.completeInitialPasswordChange.mockReset();
    mocks.loadMe.mockReset();
    mocks.login.mockReset();
    mocks.replace.mockReset();
    mocks.login.mockRejectedValue(passwordChangeRequired);
    useAuth.setState({ me: null });
  });

  it("shows the personal-password fields when login requires a password change", async () => {
    await requestInitialPasswordChange();

    expect(screen.getByText(/Временный пароль принят/)).toBeInTheDocument();
    expect(screen.getByLabelText("Новый пароль")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Повторите новый пароль")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить пароль и войти" })).toBeInTheDocument();
  });

  it("does not call the endpoint when the new passwords differ", async () => {
    const user = await requestInitialPasswordChange();
    await user.type(screen.getByLabelText("Новый пароль"), "personal-password");
    await user.type(screen.getByLabelText("Повторите новый пароль"), "different-password");

    await user.click(screen.getByRole("button", { name: "Сохранить пароль и войти" }));

    expect(screen.getByText("Новые пароли не совпадают.")).toBeInTheDocument();
    expect(mocks.completeInitialPasswordChange).not.toHaveBeenCalled();
  });

  it("changes the password and routes the client into the portal", async () => {
    // Как настоящий стор: успешный вход кладёт me, редирект делает страница.
    mocks.completeInitialPasswordChange.mockImplementation(async () => {
      useAuth.setState({ me: client });
      return client;
    });
    const user = await requestInitialPasswordChange();
    await user.type(screen.getByLabelText("Новый пароль"), "personal-password");
    await user.type(screen.getByLabelText("Повторите новый пароль"), "personal-password");

    await user.click(screen.getByRole("button", { name: "Сохранить пароль и войти" }));

    await waitFor(() =>
      expect(mocks.completeInitialPasswordChange).toHaveBeenCalledWith(
        "client-7",
        "temporary-password",
        "personal-password",
      ),
    );
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/portal/catalog"));
    expect(mocks.replace).toHaveBeenCalledTimes(1);
  });

  it("leaves forced-change mode and clears passwords when the username changes", async () => {
    const user = await requestInitialPasswordChange();
    await user.type(screen.getByLabelText("Новый пароль"), "personal-password");
    await user.type(screen.getByLabelText("Повторите новый пароль"), "personal-password");

    await user.clear(screen.getByLabelText("Логин"));
    await user.type(screen.getByLabelText("Логин"), "another-client");

    expect(screen.queryByLabelText("Новый пароль")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toHaveValue("");

    await user.type(screen.getByLabelText("Пароль"), "another-temporary-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByLabelText("Новый пароль")).toHaveValue("");
    expect(screen.getByLabelText("Повторите новый пароль")).toHaveValue("");
  });
});
