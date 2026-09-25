import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActionCard } from "./action-card";

describe("ActionCard", () => {
  it("keeps the primary link separate from tel and menu controls", () => {
    render(
      <ActionCard data-testid="card" primaryAction={{ href: "/orders/42", label: "Открыть заказ #42" }}>
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
});
