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
  role_name: null,
  client_id: 1,
  sales_department: null,
};

const factoryUser: Me = {
  ...client,
  id: 2,
  username: "factory",
  is_client: false,
  client_id: null,
  permissions: ["warehouse.view", "grain.view"],
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

  it("показывает силосы внутри «Завода», а не отдельным пунктом зерна", () => {
    nav.pathname = "/warehouse/silos";
    render(<Sidebar me={factoryUser} />);

    const factory = screen.getByRole("link", { name: "Завод" });
    expect(factory).toHaveAttribute("href", "/warehouse");
    expect(factory).toHaveClass(activeClass);
    expect(screen.getByRole("link", { name: "Приход и проход" })).toHaveAttribute("href", "/grain");
    expect(screen.queryByRole("link", { name: "Силосы" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Зерно" })).not.toBeInTheDocument();
  });

  it("ведёт сотрудника без доступа к складу сразу в силосный участок завода", () => {
    render(<Sidebar me={{ ...factoryUser, permissions: ["grain.view"] }} />);

    expect(screen.getByRole("link", { name: "Завод" })).toHaveAttribute("href", "/warehouse/silos");
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
