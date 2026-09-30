import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Topbar } from "./topbar";
import { makeMe } from "@/test-utils/factories";

const session = vi.hoisted(() => ({ push: vi.fn(), signOut: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: session.push }) }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ signOut: session.signOut }) }));
vi.mock("@/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/components/onboarding-tour", () => ({ TOUR_START_EVENT: "tour" }));

const me = makeMe({ username: "kassa", position: "Касса" });

it("shows the menu button by default", () => {
  render(<Topbar me={me} title="Касса" />);
  expect(screen.getByRole("button", { name: "Меню" })).toBeInTheDocument();
});

it("replaces the menu with a back button and renders the trailing slot", async () => {
  const onClick = vi.fn();
  render(<Topbar me={me} title="Заявки" back={{ label: "Назад в кассу", onClick }} trailing={<span>слот</span>} />);
  expect(screen.queryByRole("button", { name: "Меню" })).not.toBeInTheDocument();
  expect(screen.getByText("слот")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(onClick).toHaveBeenCalledTimes(1);
});

it("keeps theme and logout inside the profile dropdown", async () => {
  const user = userEvent.setup();
  localStorage.removeItem("asyl_theme");
  render(<Topbar me={me} title="Касса" />);

  expect(screen.queryByRole("button", { name: "Тёмная тема" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Профиль: kassa" }));

  const menu = screen.getByRole("menu", { name: "Профиль" });
  expect(menu).toHaveTextContent("Касса");
  await user.click(screen.getByRole("button", { name: "Тёмная тема" }));
  expect(document.documentElement).toHaveClass("dark");
  expect(localStorage.getItem("asyl_theme")).toBe("dark");
  expect(screen.getByRole("button", { name: "Тёмная тема" })).toHaveAttribute("aria-pressed", "true");

  // Выход по кнопке отзывает сессию на сервере, и только потом — страница входа.
  const signedOut = Promise.withResolvers<void>();
  session.signOut.mockReturnValueOnce(signedOut.promise);
  await user.click(screen.getByRole("menuitem", { name: "Выйти" }));
  expect(session.signOut).toHaveBeenCalled();
  expect(session.push).not.toHaveBeenCalled();
  signedOut.resolve();
  await waitFor(() => expect(session.push).toHaveBeenCalledWith("/login"));
  document.documentElement.classList.remove("dark");
});
