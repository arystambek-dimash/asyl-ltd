import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { WagonNumberCameraWorkspace } from "./wagon-number-camera";

const mocks = vi.hoisted(() => ({ isSuperuser: false, useApi: vi.fn() }));

vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean } }) => unknown) =>
    selector({ me: { is_superuser: mocks.isSuperuser } }),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => ({
    data: mocks.useApi(url),
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
  beforeEach(() => {
    mocks.isSuperuser = false;
    mocks.useApi.mockReset();
    mocks.useApi.mockImplementation((url: string | null) =>
      url === "/cameras/" ? [] : url ? { camera_source: "cam8", source: "main", updated_at: null } : null,
    );
  });

  it("показывает одну панель зоны арки с закреплённой камерой, без отдельного блока «Ответственная камера»", () => {
    render(<WagonNumberCameraWorkspace />);

    expect(screen.getByTestId("wagon-arch-panel")).toHaveAttribute("data-camera", "cam8");
    expect(screen.queryByText("Ответственная камера")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Назначить камеру/ })).not.toBeInTheDocument();
    expect(mocks.useApi).not.toHaveBeenCalledWith("/cameras/");
  });

  it("отдаёт кнопку назначения камеры в панель только суперадмину", () => {
    mocks.isSuperuser = true;
    render(<WagonNumberCameraWorkspace />);

    const panel = screen.getByTestId("wagon-arch-panel");
    expect(screen.getByRole("button", { name: /Назначить камеру/ })).toBeInTheDocument();
    expect(panel).toContainElement(screen.getByRole("button", { name: /Назначить камеру/ }));
    expect(mocks.useApi).toHaveBeenCalledWith("/cameras/");
  });
});
