import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WagonNumberCameraWorkspace } from "./wagon-number-camera";

vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) => ({
    data: url === "/cameras/" ? [] : null,
    error: "",
    reload: vi.fn(),
    setData: vi.fn(),
  }),
}));

describe("WagonNumberCameraWorkspace", () => {
  it("показывает рабочее место без кнопки назначения обычному сотруднику", () => {
    render(<WagonNumberCameraWorkspace />);

    expect(screen.getByText("Ответственная камера")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Назначить камеру/ })).not.toBeInTheDocument();
  });

  it("открывает назначение только суперадмину", () => {
    render(<WagonNumberCameraWorkspace canManage />);

    expect(screen.getByRole("button", { name: /Назначить камеру/ })).toBeInTheDocument();
  });
});
