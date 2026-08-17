import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PermissionPicker } from "./permission-picker";

describe("PermissionPicker", () => {
  it("показывает отдельную секцию управления AI 24/7", () => {
    render(
      <PermissionPicker
        perms={[
          {
            id: 1,
            code: "ai_247.manage",
            section: "ai_247",
            action: "manage",
            label: "AI 24/7: Управление",
          },
        ]}
        selected={new Set()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByText("AI 24/7")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Управление" })).toBeInTheDocument();
  });
});
