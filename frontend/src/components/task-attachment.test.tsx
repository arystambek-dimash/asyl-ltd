import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AttachmentChip } from "./task-attachment";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("next/image", () => import("@/test-utils/next-image"));

vi.mock("@/lib/api", () => ({
  api: { get: mocks.get },
  apiError: () => "Ошибка вложения",
  blobApiError: async () => "Ошибка вложения",
}));

describe("Task attachment renewal", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:renewed-voice"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("renews and materializes voice as a blob before native audio uses it", async () => {
    mocks.get
      .mockResolvedValueOnce({ data: { url: "/api/task-attachments/4/?token=fresh" } })
      .mockResolvedValueOnce({ data: new Blob(["OggS voice"], { type: "audio/ogg" }) });
    const user = userEvent.setup();
    const { container } = render(
      <AttachmentChip taskId={3} attachmentId={4} kind="voice" url="/expired" name="voice.ogg" />,
    );

    await user.click(screen.getByRole("button", { name: "Прослушать голосовое" }));

    await waitFor(() => expect(container.querySelector("audio")).toHaveAttribute("src", "blob:renewed-voice"));
    expect(mocks.get).toHaveBeenNthCalledWith(1, "/tasks/3/attachments/4/url/");
    expect(mocks.get).toHaveBeenNthCalledWith(2, "/api/task-attachments/4/?token=fresh", {
      responseType: "blob",
    });
  });

  it("renews a lazily loaded photo after its original URL expires", async () => {
    mocks.get.mockResolvedValueOnce({ data: { url: "/api/task-attachments/8/?token=fresh" } });
    render(<AttachmentChip taskId={7} attachmentId={8} kind="photo" url="/expired" name="photo.jpg" />);

    fireEvent.error(screen.getByRole("img", { name: "photo.jpg" }));

    await waitFor(() =>
      expect(screen.getByRole("img", { name: "photo.jpg" })).toHaveAttribute(
        "src",
        "/api/task-attachments/8/?token=fresh",
      ),
    );
    expect(mocks.get).toHaveBeenCalledWith("/tasks/7/attachments/8/url/");
  });

  it("shows why a file attachment did not open", async () => {
    const tab = { opener: {}, close: vi.fn(), location: { replace: vi.fn() } };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    mocks.get.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<AttachmentChip taskId={5} attachmentId={6} kind="file" url="/expired" name="act.pdf" />);

    await user.click(screen.getByRole("button", { name: /act\.pdf/ }));

    expect(await screen.findByText("Ошибка вложения")).toBeInTheDocument();
    expect(tab.close).toHaveBeenCalled();
  });

  it("says the file is unavailable when the server has no file any more, not «no connection»", async () => {
    const tab = { opener: {}, close: vi.fn(), location: { replace: vi.fn() } };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    mocks.get.mockResolvedValueOnce({ data: { url: null } });
    const user = userEvent.setup();
    render(<AttachmentChip taskId={5} attachmentId={6} kind="file" url="/expired" name="act.pdf" />);

    await user.click(screen.getByRole("button", { name: /act\.pdf/ }));

    expect(await screen.findByText("Файл недоступен")).toBeInTheDocument();
    expect(screen.queryByText("Ошибка вложения")).not.toBeInTheDocument();
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.replace).not.toHaveBeenCalled();
  });
});
