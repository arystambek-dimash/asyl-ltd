import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoDropZone } from "./video-drop-zone";

describe("VideoDropZone", () => {
  it("supports keyboard file selection and clear", async () => {
    const user = userEvent.setup();
    const onFile = vi.fn();
    const file = new File(["video"], "robot.mp4", { type: "video/mp4" });
    const { rerender } = render(<VideoDropZone file={null} maxBytes={100} onFile={onFile} onReject={vi.fn()} />);

    await user.upload(screen.getByLabelText(/Перетащите видео/), file);
    expect(onFile).toHaveBeenCalledWith(file);

    rerender(<VideoDropZone file={file} maxBytes={100} onFile={onFile} onReject={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Убрать видео" }));
    expect(onFile).toHaveBeenLastCalledWith(null);
  });

  it("accepts a dropped MKV and explains an unsupported format", () => {
    const onFile = vi.fn();
    const onReject = vi.fn();
    const { rerender } = render(<VideoDropZone file={null} maxBytes={100} onFile={onFile} onReject={onReject} />);
    const zone = screen.getByText(/Перетащите видео/).closest("label");
    const mkv = new File(["video"], "gazel.mkv");
    fireEvent.drop(zone!, { dataTransfer: { files: [mkv] } });
    expect(onFile).toHaveBeenCalledWith(mkv);

    rerender(<VideoDropZone file={null} maxBytes={100} onFile={onFile} onReject={onReject} />);
    const webm = new File(["video"], "gazel.webm", { type: "video/webm" });
    fireEvent.drop(screen.getByText(/Перетащите видео/).closest("label")!, {
      dataTransfer: { files: [webm] },
    });
    expect(onReject).toHaveBeenLastCalledWith(expect.stringContaining("MP4, MOV, AVI и MKV"));
  });
});
