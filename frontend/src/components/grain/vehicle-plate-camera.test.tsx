import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VehiclePlateCameraWorkspace } from "./vehicle-plate-camera";

vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="protected-camera-stream" data-src={src} />,
}));

describe("VehiclePlateCameraWorkspace", () => {
  it("shows the fixed cam1 preview through the protected camera player", () => {
    render(<VehiclePlateCameraWorkspace />);

    expect(screen.getByRole("region", { name: "Камера проходной на вывоз" })).toBeInTheDocument();
    expect(screen.getByTestId("protected-camera-stream")).toHaveAttribute("data-src", "cam1");
    expect(screen.getAllByText("Камера cam1 · OCR: main")).toHaveLength(2);
    expect(screen.getByText("Номер машины на проходной")).toBeInTheDocument();
  });
});
