import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ShippingSessionSettings } from "@/lib/shipping-sessions";
import { ShippingIdleSettings } from "./shipping-idle-settings";

const mocks = vi.hoisted(() => ({
  settings: null as ShippingSessionSettings | null,
  urls: [] as (string | null)[],
  setSettings: vi.fn(),
  patch: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: { patch: mocks.patch }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => {
    mocks.urls.push(url);
    return {
      data: mocks.settings,
      loading: false,
      error: "",
      errorStatus: null,
      reload: vi.fn(),
      setData: mocks.setSettings,
    };
  },
}));

beforeEach(() => {
  mocks.urls = [];
  mocks.settings = { idle_timeout_seconds: 300, can_manage: true };
});

describe("ShippingIdleSettings", () => {
  it("saves an operator's idle timeout in seconds and explains existing segment snapshots", async () => {
    const user = userEvent.setup();
    mocks.patch.mockResolvedValue({ data: { idle_timeout_seconds: 450, can_manage: true } });
    render(<ShippingIdleSettings />);
    expect(mocks.urls).toContain("/cameras/shipping-session-settings/");
    await user.click(screen.getByRole("button", { name: "Простой: 5 мин." }));
    const modal = screen.getByRole("dialog");
    expect(within(modal).getByText(/открытые сохраняют прежнюю настройку/)).toBeInTheDocument();
    const input = within(modal).getByRole("spinbutton", { name: "Простой, минут" });
    await user.clear(input);
    await user.type(input, "7.5");
    await user.click(within(modal).getByRole("button", { name: "Сохранить настройку" }));
    expect(mocks.patch).toHaveBeenCalledWith("/cameras/shipping-session-settings/", { idle_timeout_seconds: 450 });
    expect(mocks.setSettings).toHaveBeenCalledWith({ idle_timeout_seconds: 450, can_manage: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not claim a setting was saved on permission failure", async () => {
    const user = userEvent.setup();
    mocks.patch.mockRejectedValue(new Error("Недостаточно прав"));
    render(<ShippingIdleSettings />);
    await user.click(screen.getByRole("button", { name: "Простой: 5 мин." }));
    await user.click(screen.getByRole("button", { name: "Сохранить настройку" }));
    const modal = screen.getByRole("dialog");
    expect(within(modal).getByRole("alert")).toHaveTextContent("Недостаточно прав");
    expect(mocks.setSettings).not.toHaveBeenCalled();
  });

  it("shows the timeout read-only to staff who cannot change it", () => {
    mocks.settings = { idle_timeout_seconds: 300, can_manage: false };
    render(<ShippingIdleSettings />);
    expect(screen.getByText("Закрытие по простою: 5 мин.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Простой:/ })).toBeNull();
  });
});
