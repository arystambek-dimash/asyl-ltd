import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Topbar } from "./topbar";
import type { Me } from "@/lib/types";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ logout: vi.fn() }) }));
vi.mock("@/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/components/onboarding-tour", () => ({ TOUR_START_EVENT: "tour" }));

const me: Me = {
  id: 1,
  username: "kassa",
  is_client: false,
  is_superuser: false,
  permissions: [],
  position: "Касса",
  client_id: null,
  sales_department: null,
};

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
