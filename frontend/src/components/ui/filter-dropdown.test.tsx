import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { FilterDropdown } from "./filter-dropdown";

const options = [
  { key: "all", label: "Все" },
  { key: "active", label: "Активные", count: 4 },
  { key: "done", label: "Готовые" },
];

function Fixture() {
  const [active, setActive] = useState("active");
  return <FilterDropdown label="Статус" options={options} active={active} onChange={setActive} />;
}

describe("FilterDropdown", () => {
  it("links its trigger and listbox and focuses the selected option", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    const trigger = screen.getByRole("button", { name: /Статус:/ });

    await user.click(trigger);

    const listbox = screen.getByRole("listbox", { name: "Статус" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", listbox.id);
    expect(screen.getByRole("option", { name: "Активные, 4" })).toHaveFocus();
  });

  it("supports roving keyboard navigation, selection and focus restore", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    const trigger = screen.getByRole("button", { name: /Статус:/ });
    trigger.focus();

    await user.keyboard("{ArrowDown}{End}{Enter}");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveTextContent("Готовые");

    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("option", { name: "Готовые" })).toHaveFocus();
    await user.keyboard("{Home}{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
