import { useCallback, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";
import { Sidebar } from "./sidebar";

vi.mock("next/navigation", () => ({ usePathname: () => "/portal/catalog" }));

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
