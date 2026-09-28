import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Permission } from "@/lib/types";
import { PermissionPicker } from "./permission-picker";

function perm(code: string, label = code, sectionLabel = code.split(".")[0]): Permission {
  const [section, action] = code.split(".");
  return { id: code.length, code, section, action, label, section_label: sectionLabel };
}

describe("PermissionPicker", () => {
  it("называет разделы как страницы меню и показывает действие без раздела", () => {
    render(
      <PermissionPicker
        perms={[perm("monoblock.view", "Моноблок: Доступ (видит всё)", "Моноблок")]}
        selected={new Set()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Моноблок" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Доступ (видит всё)" })).toBeInTheDocument();
  });

  it("подписывает каждый раздел с сервера: магазины и бот не показываются сырым кодом", () => {
    render(
      <PermissionPicker
        perms={[
          perm("stores.view", "Магазины: Просмотр", "Магазины"),
          perm("bots.view", "Telegram-бот: Журнал", "Telegram-бот"),
        ]}
        selected={new Set()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual(["Магазины", "Telegram-бот"]);
  });

  it("сохраняет порядок разделов с сервера (порядок меню), а не сортирует по алфавиту", () => {
    render(
      <PermissionPicker
        perms={[
          perm("orders.view", "orders.view", "Заказы"),
          perm("monoblock.view", "monoblock.view", "Моноблок"),
          perm("orders.create", "orders.create", "Заказы"),
          perm("tasks.view", "tasks.view", "Задачи"),
        ]}
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

  it("даёт отдельно скрыть «Главную» и страницу «Задачи»", () => {
    render(
      <PermissionPicker
        perms={[
          perm("dashboard.view", "Главная: Доступ", "Главная"),
          perm("tasks.own", "Задачи: Доступ (свои задачи)", "Задачи"),
          perm("tasks.view", "Задачи: Задачи всех сотрудников", "Задачи"),
        ]}
        selected={new Set(["tasks.own"])}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual(["Главная", "Задачи"]);
    expect(screen.getByRole("button", { name: "Доступ" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Доступ (свои задачи)" })).toBeInTheDocument();
  });
});
