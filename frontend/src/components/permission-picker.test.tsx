import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Permission } from "@/lib/types";
import { PermissionPicker } from "./permission-picker";

function perm(code: string, label = code): Permission {
  const [section, action] = code.split(".");
  return { id: code.length, code, section, action, label };
}

describe("PermissionPicker", () => {
  it("называет разделы как страницы меню и показывает действие без раздела", () => {
    render(
      <PermissionPicker
        perms={[perm("monoblock.view", "Моноблок: Доступ (видит всё)")]}
        selected={new Set()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Моноблок" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Доступ (видит всё)" })).toBeInTheDocument();
  });

  it("ставит разделы в порядке меню, а не по алфавиту", () => {
    render(
      <PermissionPicker
        perms={[perm("tasks.view"), perm("orders.view"), perm("monoblock.view")]}
        selected={new Set()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Заказы",
      "Моноблок",
      "Задачи",
    ]);
  });
});
