import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Tabs } from "./tabs";

const tabs = [
  { key: "overview", label: "Обзор" },
  { key: "payments", label: "Оплаты", count: 2 },
  { key: "history", label: "История" },
];

function Fixture({ variant = "underline" }: { variant?: "underline" | "segment" }) {
  const [active, setActive] = useState("overview");
  return <Tabs tabs={tabs} active={active} onChange={setActive} variant={variant} />;
}

describe("Tabs", () => {
  it.each(["underline", "segment"] as const)("exposes tab semantics for the %s variant", (variant) => {
    render(<Fixture variant={variant} />);

    expect(screen.getByRole("tablist", { name: "Разделы" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Обзор" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Оплаты, 2" })).toHaveAttribute("aria-selected", "false");
  });

  it("supports arrow, Home and End navigation with roving focus", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    const overview = screen.getByRole("tab", { name: "Обзор" });
    overview.focus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Оплаты, 2" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Оплаты, 2" })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "История" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(overview).toHaveFocus();

    await user.keyboard("{End}{Home}");
    expect(overview).toHaveFocus();
  });
});
