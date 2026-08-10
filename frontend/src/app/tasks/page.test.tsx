import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AttachmentChip } from "@/components/task-attachment";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("next/image", () => ({
  default: ({ alt, unoptimized, ...props }: ComponentProps<"img"> & { unoptimized?: boolean }) => {
    void unoptimized;
    // Next image optimization is unrelated to signed attachment renewal.
    // eslint-disable-next-line @next/next/no-img-element
    return <img alt={alt ?? ""} {...props} />;
  },
}));

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
});
