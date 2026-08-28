import { useCallback, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";
import { Sidebar } from "./sidebar";

const nav = vi.hoisted(() => ({ pathname: "/portal/catalog" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));

beforeEach(() => {
  nav.pathname = "/portal/catalog";
});

const client: Me = {
  id: 1,
  username: "client",
  is_client: true,
  is_superuser: false,
  is_monoblock: false,
  monoblock_name: null,
  monoblock_camera: null,
  permissions: [],
  position: null,
  client_id: 1,
  sales_department: null,
};

const factoryUser: Me = {
  ...client,
  id: 2,
  username: "factory",
  is_client: false,
  client_id: null,
  permissions: ["warehouse.view", "silos.view", "grain.view"],
};

function Harness() {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Меню
      </button>
      <Sidebar me={client} mobileOpen={open} onClose={close} />
      <button type="button">Внешняя кнопка</button>
    </>
  );
}

describe("подсветка активного пункта", () => {
  const activeClass = "font-medium";

  it("на «Новый заказ» горит только он, без «Мои заказы»", () => {
    nav.pathname = "/portal/orders/new";
    render(<Sidebar me={client} />);
    expect(screen.getByRole("link", { name: "Новый заказ" })).toHaveClass(activeClass);
    expect(screen.getByRole("link", { name: "Мои заказы" })).not.toHaveClass(activeClass);
  });

  it("на списке заказов горят «Мои заказы», а не «Новый заказ»", () => {
    nav.pathname = "/portal/orders";
    render(<Sidebar me={client} />);
    expect(screen.getByRole("link", { name: "Мои заказы" })).toHaveClass(activeClass);
    expect(screen.getByRole("link", { name: "Новый заказ" })).not.toHaveClass(activeClass);
  });

  it("на деталке заказа по-прежнему горят «Мои заказы»", () => {
    nav.pathname = "/portal/orders/42";
    render(<Sidebar me={client} />);
    expect(screen.getByRole("link", { name: "Мои заказы" })).toHaveClass(activeClass);
    expect(screen.getByRole("link", { name: "Новый заказ" })).not.toHaveClass(activeClass);
  });

  it("показывает склад и силосы отдельными пунктами, активен самый специфичный", () => {
    nav.pathname = "/warehouse/silos";
    render(<Sidebar me={factoryUser} />);

    const stock = screen.getByRole("link", { name: "Склад" });
    expect(stock).toHaveAttribute("href", "/warehouse");
    expect(stock).not.toHaveClass(activeClass);
    const silos = screen.getByRole("link", { name: "Силосы" });
    expect(silos).toHaveAttribute("href", "/warehouse/silos");
    expect(silos).toHaveClass(activeClass);
    expect(screen.getByRole("link", { name: "Приход и вывоз" })).toHaveAttribute("href", "/grain");
  });

  it("прячет вкладки склада и силосов без соответствующих прав", () => {
    render(<Sidebar me={{ ...factoryUser, permissions: ["grain.view"] }} />);

    expect(screen.queryByRole("link", { name: "Склад" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Силосы" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Приход и вывоз" })).toHaveAttribute("href", "/grain");
  });

  it("показывает кассу сотруднику, который может вносить оплаты", () => {
    render(<Sidebar me={{ ...factoryUser, permissions: ["payments.create"] }} />);

    expect(screen.getByRole("link", { name: "Касса" })).toHaveAttribute("href", "/accounting");
    expect(screen.queryByRole("link", { name: "Отчёты" })).not.toBeInTheDocument();
  });

  it("показывает журнал машин только с правом просмотра событий", () => {
    const { rerender } = render(<Sidebar me={{ ...factoryUser, permissions: ["events.view"] }} />);

    expect(screen.getByRole("link", { name: "Журнал машин" })).toHaveAttribute("href", "/vehicle-plate-events");

    rerender(<Sidebar me={{ ...factoryUser, permissions: [] }} />);
    expect(screen.queryByRole("link", { name: "Журнал машин" })).not.toBeInTheDocument();
  });

  it("показывает лабораторию моделей только superuser", () => {
    const { rerender } = render(<Sidebar me={factoryUser} />);
    expect(screen.queryByRole("link", { name: "Тест моделей" })).not.toBeInTheDocument();

    rerender(<Sidebar me={{ ...factoryUser, is_superuser: true }} />);
    expect(screen.getByRole("link", { name: "Тест моделей" })).toHaveAttribute("href", "/management/model-tests");
  });
});

describe("mobile Sidebar", () => {
  it("moves, traps, and restores keyboard focus", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Меню" });

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Меню навигации" });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Закрыть меню" })).toHaveFocus());

    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(screen.getByRole("button", { name: "Внешняя кнопка" })).not.toHaveFocus();

    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });
});
