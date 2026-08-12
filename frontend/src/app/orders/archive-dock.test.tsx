import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ArchiveDock } from "@/components/orders/archive-dock";
import type { Order } from "@/lib/types";

const deleteMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { delete: deleteMock, post: vi.fn() },
  apiError: () => "Не удалось удалить заказ из архива",
}));

const archivedOrder = {
  id: 7,
  client: 3,
  client_name: "ТОО Цех",
  total_amount: "120000",
  currency: "KZT",
  deleted_at: "2026-07-30T10:00:00Z",
} as Order;

describe("ArchiveDock", () => {
  beforeEach(() => {
    deleteMock.mockReset();
  });

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
    const purge = screen.getByTitle("Удалить из архива");
    await user.click(purge);

    expect(screen.getByRole("dialog", { name: "Удалить заказ из архива?" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Последние заказы в архиве" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "Удалить заказ из архива?" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Последние заказы в архиве" })).toBeInTheDocument();
    expect(purge).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Последние заказы в архиве" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps the purge confirmation open and exposes the API error when deletion fails", async () => {
    deleteMock.mockRejectedValue(new Error("protected financial record"));
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<ArchiveDock trashed={[archivedOrder]} count={1} onOpenArchive={vi.fn()} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: "Архив заказов" }));
    await user.click(screen.getByTitle("Удалить из архива"));
    const confirmation = screen.getByRole("dialog", { name: "Удалить заказ из архива?" });
    await user.click(within(confirmation).getByRole("button", { name: "Удалить из архива" }));

    expect(deleteMock).toHaveBeenCalledWith("/orders/7/purge/");
    expect(await screen.findByText("Не удалось удалить заказ из архива")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Удалить заказ из архива?" })).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("closes and refreshes the archive only after a successful purge", async () => {
    deleteMock.mockResolvedValue({ status: 204 });
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<ArchiveDock trashed={[archivedOrder]} count={1} onOpenArchive={vi.fn()} onChanged={onChanged} />);

    await user.click(screen.getByRole("button", { name: "Архив заказов" }));
    await user.click(screen.getByTitle("Удалить из архива"));
    const confirmation = screen.getByRole("dialog", { name: "Удалить заказ из архива?" });
    expect(confirmation).toHaveTextContent("Проведённые оплаты, отгрузка и история AI останутся в учёте.");
    await user.click(within(confirmation).getByRole("button", { name: "Удалить из архива" }));

    expect(deleteMock).toHaveBeenCalledWith("/orders/7/purge/");
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(screen.queryByRole("dialog", { name: "Удалить заказ из архива?" })).not.toBeInTheDocument();
  });
});
