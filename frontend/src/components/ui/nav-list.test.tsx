import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Users } from "lucide-react";
import { expect, it, vi } from "vitest";
import { NavList } from "./nav-list";

it("renders links and buttons with their titles", async () => {
  const onSelect = vi.fn();
  render(
    <NavList
      label="Разделы"
      items={[
        { key: "debts", icon: Users, title: "Долги клиентов", subtitle: "12 клиентов", href: "/accounting?view=debts" },
        { key: "journal", icon: Users, title: "Журнал", value: "3", onSelect },
      ]}
    />,
  );
  expect(screen.getByRole("link", { name: /Долги клиентов/ })).toHaveAttribute("href", "/accounting?view=debts");
  expect(screen.getByText("12 клиентов")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Журнал/ }));
  expect(onSelect).toHaveBeenCalledTimes(1);
});
