import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import ModelTestsPage from "./page";

vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children, superuserOnly }: { children: ReactNode; superuserOnly?: boolean }) => (
    <div data-superuser-only={String(superuserOnly)}>{children}</div>
  ),
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("@/components/model-tests/model-test-workbench", () => ({
  ModelTestWorkbench: () => <div>model workbench</div>,
}));

describe("ModelTestsPage", () => {
  it("uses the explicit superuser guard", () => {
    render(<ModelTestsPage />);
    expect(screen.getByText("model workbench").closest("[data-superuser-only]")).toHaveAttribute(
      "data-superuser-only",
      "true",
    );
  });
});
