import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CameraTile, type CameraFeed } from "./camera-wall";

vi.mock("@/components/camera-stream", () => ({
  CameraStream: () => null,
  ensureCameraStreamToken: vi.fn(),
}));

const camera: CameraFeed & { src: string } = {
  id: "direct-aa:bb",
  name: "cam1",
  zone: "Главные ворота",
  src: "cam1",
  kind: "direct",
  online: true,
};

describe("CameraTile", () => {
  it("opens through a native keyboard-accessible action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();

    render(<CameraTile cam={camera} ready={false} onOnline={vi.fn()} onClick={onClick} />);

    const open = screen.getByRole("button", { name: "Открыть камеру «Главные ворота»" });
    await user.tab();
    expect(open).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("keeps camera controls separate from the tile action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const onRename = vi.fn();
    const onConfigureLine = vi.fn();

    render(
      <CameraTile
        cam={camera}
        ready={false}
        onOnline={vi.fn()}
        onClick={onClick}
        onRename={onRename}
        onConfigureLine={onConfigureLine}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Изменить название камеры" }));
    await user.click(screen.getByRole("button", { name: "Настроить линию подсчёта" }));

    expect(onRename).toHaveBeenCalledWith(camera);
    expect(onConfigureLine).toHaveBeenCalledWith(camera);
    expect(onClick).not.toHaveBeenCalled();
  });
});
