import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ActionMenu } from "./action-menu";

describe("ActionMenu", () => {
  it("closes when its trigger is clicked a second time", async () => {
    const user = userEvent.setup();
    render(<ActionMenu label="Действия заказа" items={[{ key: "edit", label: "Редактировать", onSelect: vi.fn() }]} />);

    const trigger = screen.getByRole("button", { name: "Действия заказа" });
    await user.click(trigger);

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitem", { name: "Редактировать" })).toHaveFocus();

    await user.click(trigger);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("supports arrow navigation and restores trigger focus on Escape", async () => {
    const user = userEvent.setup();
    render(
      <ActionMenu
        label="Действия заказа"
        items={[
          { key: "edit", label: "Редактировать", onSelect: vi.fn() },
          { key: "delete", label: "Удалить", onSelect: vi.fn() },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Действия заказа" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Редактировать" })).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Удалить" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("restores trigger focus before selection and when Tab closes the menu", async () => {
    const user = userEvent.setup();
    let focusedAtSelection: Element | null = null;
    render(
      <ActionMenu
        label="Действия заказа"
        items={[
          {
            key: "edit",
            label: "Редактировать",
            onSelect: () => {
              focusedAtSelection = document.activeElement;
            },
          },
        ]}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Действия заказа" });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Редактировать" }));
    expect(focusedAtSelection).toBe(trigger);

    await user.click(trigger);
    await user.keyboard("{Tab}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
