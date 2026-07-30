import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArchiveDock } from "@/components/orders/archive-dock";
import type { Order } from "@/lib/types";

const archivedOrder = {
  id: 7,
  client: 3,
  client_name: "ТОО Цех",
  total_amount: "120000",
  currency: "KZT",
  deleted_at: "2026-07-30T10:00:00Z",
} as Order;

describe("ArchiveDock", () => {
  it("exposes a related dialog, enters it, and restores trigger focus on Escape", async () => {
    const user = userEvent.setup();
    render(<ArchiveDock trashed={[]} count={0} onOpenArchive={vi.fn()} onChanged={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "Архив заказов" });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Последние заказы в архиве" });
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", dialog.id);
    await waitFor(() => expect(screen.getByRole("button", { name: "Открыть архив" })).toHaveFocus());

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "Последние заказы в архиве" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("allows Tab to leave the non-modal preview and closes it on focus exit", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Следующее действие</button>
        <ArchiveDock trashed={[]} count={0} onOpenArchive={vi.fn()} onChanged={vi.fn()} />
      </>,
    );

    const trigger = screen.getByRole("button", { name: "Архив заказов" });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole("button", { name: "Открыть архив" })).toHaveFocus());

    await user.tab();
    expect(trigger).toHaveFocus();
    await user.tab();

    expect(trigger).not.toHaveFocus();
    expect(screen.queryByRole("dialog", { name: "Последние заказы в архиве" })).not.toBeInTheDocument();
  });

  it("keeps a stable return target before opening the full archive", async () => {
    const user = userEvent.setup();
    let focusedAtOpen: Element | null = null;
    render(
      <ArchiveDock
        trashed={[]}
        count={3}
        onOpenArchive={() => {
          focusedAtOpen = document.activeElement;
        }}
        onChanged={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: "Архив заказов" });
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Открыть архив (3)" }));

    expect(focusedAtOpen).toBe(trigger);
    expect(screen.queryByRole("dialog", { name: "Последние заказы в архиве" })).not.toBeInTheDocument();
  });

  it("keeps the preview open while its nested confirmation dialog owns focus", async () => {
    const user = userEvent.setup();
    render(<ArchiveDock trashed={[archivedOrder]} count={1} onOpenArchive={vi.fn()} onChanged={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "Архив заказов" });
    await user.click(trigger);
    const purge = screen.getByTitle("Удалить навсегда");
    await user.click(purge);

    expect(screen.getByRole("dialog", { name: "Удалить заказ навсегда?" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Последние заказы в архиве" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "Удалить заказ навсегда?" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Последние заказы в архиве" })).toBeInTheDocument();
    expect(purge).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Последние заказы в архиве" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
