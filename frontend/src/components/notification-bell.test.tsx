import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationBell } from "./notification-bell";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  reload: vi.fn(),
  showToast: vi.fn(),
  result: {
    data: undefined as { id: number; text: string; is_read: boolean; created_at: string }[] | undefined,
    loading: false,
    error: "",
  },
}));

vi.mock("@/lib/use-api", () => ({
  useApi: () => ({
    ...mocks.result,
    reload: mocks.reload,
  }),
}));
vi.mock("@/lib/api", () => ({
  api: { post: mocks.post },
  apiError: () => "Не удалось обновить уведомление",
}));
vi.mock("@/lib/toast", () => ({ showToast: mocks.showToast }));

describe("NotificationBell", () => {
  beforeEach(() => {
    mocks.post.mockReset().mockResolvedValue({});
    mocks.reload.mockReset().mockResolvedValue(undefined);
    mocks.showToast.mockReset();
    mocks.result.data = [
      { id: 1, text: "Новый заказ", is_read: false, created_at: "2026-07-30T10:00:00Z" },
      { id: 2, text: "Архив обновлён", is_read: true, created_at: "2026-07-29T10:00:00Z" },
    ];
    mocks.result.loading = false;
    mocks.result.error = "";
  });

  it("exposes its unread count and restores trigger focus on Escape", async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    const trigger = screen.getByRole("button", { name: "Уведомления: 1 непрочитанных" });

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Уведомления" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(trigger).toHaveAttribute("aria-controls", dialog.id);

    await user.tab();
    expect(screen.getByRole("button", { name: /Новый заказ/ })).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("marks an unread notification and reloads the list", async () => {
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления: 1 непрочитанных" }));
    await user.click(screen.getByRole("button", { name: /Новый заказ/ }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/portal/notifications/1/read/"));
    expect(mocks.reload).toHaveBeenCalledOnce();
  });

  it("reports a failed mark-read action instead of swallowing it", async () => {
    mocks.post.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<NotificationBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления: 1 непрочитанных" }));
    await user.click(screen.getByRole("button", { name: /Новый заказ/ }));

    await waitFor(() => expect(mocks.showToast).toHaveBeenCalledWith("Не удалось обновить уведомление"));
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("shows a retry state instead of claiming an empty inbox on load error", async () => {
    mocks.result.data = undefined;
    mocks.result.error = "Уведомления недоступны";
    const user = userEvent.setup();
    render(<NotificationBell />);

    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Уведомления недоступны");
    expect(screen.queryByText("Нет уведомлений.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(mocks.reload).toHaveBeenCalledOnce();
  });
});
