import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ActionCard } from "./action-card";

describe("ActionCard", () => {
  it("keeps the primary link separate from tel and menu controls", () => {
    render(
      <ActionCard data-testid="card" primaryAction={{ kind: "link", href: "/orders/42", label: "Открыть заказ #42" }}>
        <span>Заказ #42</span>
        <a href="tel:+77010000000">Позвонить</a>
        <button type="button">Действия</button>
      </ActionCard>,
    );

    const card = screen.getByTestId("card");
    const primaryLink = screen.getByRole("link", { name: "Открыть заказ #42" });
    const phoneLink = screen.getByRole("link", { name: "Позвонить" });
    const menu = screen.getByRole("button", { name: "Действия" });

    expect(card).not.toHaveAttribute("role");
    expect(card).not.toHaveAttribute("tabindex");
    expect(primaryLink.parentElement).toBe(card);
    expect(primaryLink).not.toContainElement(phoneLink);
    expect(primaryLink).not.toContainElement(menu);
  });

  it("uses a native keyboard-operable button for non-navigation actions", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onSecondary = vi.fn();
    render(
      <ActionCard primaryAction={{ kind: "button", label: "Открыть историю заказа #7", onSelect }}>
        <span>Заказ #7</span>
        <button type="button" onClick={onSecondary}>
          Завершить
        </button>
      </ActionCard>,
    );

    const action = screen.getByRole("button", { name: "Открыть историю заказа #7" });
    await user.click(screen.getByRole("button", { name: "Завершить" }));
    expect(onSecondary).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();

    action.focus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");

    expect(onSelect).toHaveBeenCalledTimes(2);
  });
});
