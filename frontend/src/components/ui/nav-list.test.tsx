import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Users } from "lucide-react";
import { expect, it, vi } from "vitest";
import { NavList } from "./nav-list";

it("renders buttons with their titles and subtitles", async () => {
  const onSelect = vi.fn();
  render(
    <NavList
      label="Разделы"
      items={[{ key: "debts", icon: Users, title: "Долги клиентов", subtitle: "12 клиентов", onSelect }]}
    />,
  );
  expect(screen.getByText("12 клиентов")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Долги клиентов/ }));
  expect(onSelect).toHaveBeenCalledTimes(1);
});
