import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { WagonNumberCameraWorkspace } from "./wagon-number-camera";

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) => ({
    data: url === "/cameras/" ? [] : { camera_source: "cam8", sync_status: "synced" },
    error: "",
    reload: vi.fn(),
    setData: vi.fn(),
  }),
}));
vi.mock("./wagon-arch-camera", () => ({
  WagonArchCameraPanel: ({ assignAction, assignedCamera }: { assignAction?: ReactNode; assignedCamera?: string }) => (
    <div data-testid="wagon-arch-panel" data-camera={assignedCamera ?? ""}>
      {assignAction}
    </div>
  ),
}));

describe("WagonNumberCameraWorkspace", () => {
  it("показывает одну панель зоны арки с закреплённой камерой, без отдельного блока «Ответственная камера»", () => {
    render(<WagonNumberCameraWorkspace />);

    expect(screen.getByTestId("wagon-arch-panel")).toHaveAttribute("data-camera", "cam8");
    expect(screen.queryByText("Ответственная камера")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Назначить камеру/ })).not.toBeInTheDocument();
  });

  it("отдаёт кнопку назначения камеры в панель только суперадмину", () => {
    render(<WagonNumberCameraWorkspace canManage />);

    const panel = screen.getByTestId("wagon-arch-panel");
    expect(screen.getByRole("button", { name: /Назначить камеру/ })).toBeInTheDocument();
    expect(panel).toContainElement(screen.getByRole("button", { name: /Назначить камеру/ }));
  });
});
