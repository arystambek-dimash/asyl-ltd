import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Camera } from "lucide-react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="camera-stream" data-src={src} />,
}));

import { CameraChoice } from "./camera-choice";
import type { PlayableCamera } from "@/lib/shipping-cameras";

const camera: PlayableCamera = {
  id: "nvr-1",
  name: "Канал 1",
  zone: "Конвейер",
  src: "cam1",
  kind: "nvr-channel",
  online: true,
};

describe("CameraChoice", () => {
  it("показывает кадр камеры и переключает выбор", async () => {
    const onToggle = vi.fn();
    render(<CameraChoice camera={camera} checked accent="blue" icon={Camera} onToggle={onToggle} />);

    expect(screen.getByTestId("camera-stream")).toHaveAttribute("data-src", "cam1");
    expect(screen.getByText("НЕТ СИГНАЛА")).toBeInTheDocument();
    const button = screen.getByRole("button", { pressed: true });
    await userEvent.click(button);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("блокирует камеру другого контура и объясняет причину", async () => {
    const onToggle = vi.fn();
    render(
      <CameraChoice
        camera={camera}
        checked={false}
        accent="amber"
        icon={Camera}
        disabled
        disabledReason="занята контуром AI 24/7"
        onToggle={onToggle}
      />,
    );

    const button = screen.getByRole("button", { name: "Конвейер: занята контуром AI 24/7" });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(onToggle).not.toHaveBeenCalled();
  });
});
