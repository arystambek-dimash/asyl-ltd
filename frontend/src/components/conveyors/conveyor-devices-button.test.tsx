import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CameraFeed } from "@/components/camera-wall";
import { ConveyorDevicesButton } from "@/components/conveyors/conveyor-devices-button";
import type { ConveyorDevice, ConveyorDeviceEnrollment } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());
const successMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: () => "Не удалось создать привязку",
}));

vi.mock("@/lib/toast", () => ({ showSuccess: successMock }));

const cameras = [
  {
    id: "camera-2",
    name: "Camera 2",
    zone: "Камера 2",
    src: "cam2",
    kind: "nvr-channel",
    online: true,
  },
  {
    id: "camera-3",
    name: "Camera 3",
    zone: "Камера 3",
    src: "cam3",
    kind: "nvr-channel",
    online: true,
  },
] satisfies Array<CameraFeed & { src: string }>;

function device(overrides: Partial<ConveyorDevice> = {}): ConveyorDevice {
  return {
    id: 1,
    public_id: "11111111-1111-4111-8111-111111111111",
    name: "ESP32 B3EDD0",
    camera_source: "cam2",
    is_active: true,
    desired_state: 0,
    command_revision: 1,
    command_session_id: null,
    command_target_total: null,
    command_terminal: true,
    stop_reason: "",
    last_seen_at: null,
    output_state: 0,
    feedback_state: 0,
    fault: null,
    firmware: "1.0.0",
    wifi_rssi: -55,
    last_ai_seen_at: null,
    last_total: 0,
    created_at: "2026-08-15T09:00:00Z",
    updated_at: "2026-08-15T09:00:00Z",
    ...overrides,
  };
}

function enrollment(): ConveyorDeviceEnrollment {
  return {
    ...device({
      id: 2,
      public_id: "22222222-2222-4222-8222-222222222222",
      name: "ESP32 · Камера 3",
      camera_source: "cam3",
    }),
    credential: {
      device_id: "22222222-2222-4222-8222-222222222222",
      token: "device-secret-token",
      authorization: "Device 22222222-2222-4222-8222-222222222222.device-secret-token",
    },
  };
}

describe("ConveyorDevicesButton", () => {
  beforeEach(() => {
    postMock.mockReset();
    successMock.mockReset();
  });

  it("показывает занятую и свободную камеры отдельно", async () => {
    const user = userEvent.setup();
    const reload = vi.fn().mockResolvedValue(undefined);
    render(<ConveyorDevicesButton cameras={cameras} devices={[device()]} reload={reload} />);

    await user.click(screen.getByRole("button", { name: /ESP32/ }));

    const dialog = screen.getByRole("dialog", { name: "ESP32 по камерам" });
    expect(within(dialog).getByText(/дополнительных настроек режима не нужно/)).toBeInTheDocument();
    expect(within(dialog).getByText("ESP32 B3EDD0")).toBeInTheDocument();
    expect(within(dialog).getByText("cam2")).toBeInTheDocument();
    expect(within(dialog).getByText("Камера 3")).toBeInTheDocument();
    expect(within(dialog).getAllByText("Свободна")).toHaveLength(1);
    expect(reload).toHaveBeenCalledOnce();
  });

  it("отправляет точный POST и сразу показывает одноразовый token", async () => {
    postMock.mockResolvedValue({ data: enrollment() });
    const reload = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ConveyorDevicesButton cameras={cameras} devices={[device()]} reload={reload} />);

    await user.click(screen.getByRole("button", { name: /ESP32/ }));
    const manager = screen.getByRole("dialog", { name: "ESP32 по камерам" });
    await user.click(within(manager).getByRole("button", { name: "Привязать" }));

    const form = screen.getByRole("dialog", { name: "Привязать ESP32" });
    expect(within(form).getByLabelText("Камера")).toHaveValue("cam3");
    expect(within(form).getByLabelText("Название ESP32")).toHaveValue("ESP32 · Камера 3");
    expect(within(form).queryByRole("option", { name: /cam2/ })).not.toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Создать привязку" }));

    expect(postMock).toHaveBeenCalledWith("/conveyors/devices/", {
      name: "ESP32 · Камера 3",
      camera_source: "cam3",
      is_active: true,
    });
    const credentialDialog = await screen.findByRole("dialog", { name: "ESP32 привязан" });
    expect(within(credentialDialog).getAllByText(/22222222-2222-4222-8222-222222222222/)).toHaveLength(2);
    expect(within(credentialDialog).getByText(/device-secret-token/)).toBeInTheDocument();
    expect(successMock).toHaveBeenCalledWith("ESP32 закреплён за cam3");
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("сохраняет token на экране, даже если обновление списка упало", async () => {
    postMock.mockResolvedValue({ data: enrollment() });
    const reload = vi.fn().mockRejectedValue(new Error("offline"));
    const user = userEvent.setup();
    render(<ConveyorDevicesButton cameras={cameras} devices={[]} reload={reload} />);

    await user.click(screen.getByRole("button", { name: /ESP32/ }));
    await user.click(
      within(screen.getByRole("dialog", { name: "ESP32 по камерам" })).getByRole("button", {
        name: "Привязать ESP32",
      }),
    );
    await user.click(
      within(screen.getByRole("dialog", { name: "Привязать ESP32" })).getByRole("button", {
        name: "Создать привязку",
      }),
    );

    const credentialDialog = await screen.findByRole("dialog", { name: "ESP32 привязан" });
    expect(within(credentialDialog).getByText(/device-secret-token/)).toBeInTheDocument();
    expect(screen.queryByText("Не удалось создать привязку")).not.toBeInTheDocument();
  });
});
