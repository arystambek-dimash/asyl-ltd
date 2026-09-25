import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import TasksPage from "./page";

vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { permissions: ["tasks.create"] }, loading: false }),
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/voice-recorder", () => ({ VoiceRecorder: () => null }));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: [], loading: false, error: "", reload: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
  apiError: () => "Ошибка",
}));

describe("Task photos preview", () => {
  it("показывает два снимка с одинаковым именем без коллизии ключей", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    render(<TasksPage />);

    await user.click(screen.getByRole("button", { name: "Поставить задачу" }));
    await user.click(screen.getByRole("button", { name: "Срок, голос, фото" }));
    // Камера телефона часто называет каждый снимок image.jpg.
    await user.upload(screen.getByLabelText("Фото"), [
      new File(["a"], "image.jpg", { type: "image/jpeg" }),
      new File(["b"], "image.jpg", { type: "image/jpeg" }),
    ]);

    const duplicateKeyWarned = consoleError.mock.calls.some((call) => String(call[0]).includes("same key"));
    consoleError.mockRestore();
    expect(screen.getAllByText("image.jpg")).toHaveLength(2);
    expect(duplicateKeyWarned).toBe(false);
  });
});
