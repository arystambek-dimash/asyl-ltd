import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TruckScalePreview } from "@/lib/types";
import { LiveScaleStatus } from "./live-scale-status";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  reload: vi.fn(),
  useVisiblePolling: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number, active?: boolean) =>
    mocks.useVisiblePolling(poll, intervalMs, active),
}));

function preview(overrides: Partial<TruckScalePreview> = {}): TruckScalePreview {
  return {
    state: "ready",
    enabled: true,
    ready: true,
    capturable: true,
    connected: true,
    stable: true,
    stale: false,
    weight_kg: "3660.00",
    age_seconds: "0.2",
    updated_at: "2026-08-12T12:20:02+05:00",
    observed_at: "2026-08-12T12:20:02+05:00",
    refresh_mode: "manual",
    ...overrides,
  };
}

function mockApi(data: TruckScalePreview | null, error = "", loading = false) {
  mocks.useApi.mockReturnValue({ data, error, loading, reload: mocks.reload });
}

describe("LiveScaleStatus", () => {
  beforeEach(() => {
    mocks.useApi.mockReset();
    mocks.reload.mockReset();
    mocks.useVisiblePolling.mockReset();
    mocks.reload.mockResolvedValue(undefined);
  });

  it("shows the current stable truck weight and keeps it live while the tab is visible", () => {
    mockApi(preview());

    render(<LiveScaleStatus active scaleKey="truck" label="Вывоз" />);

    expect(mocks.useApi).toHaveBeenCalledWith("/truck-scales/truck/reading/");
    expect(mocks.useVisiblePolling).toHaveBeenCalledWith(mocks.reload, 3000, true);
    expect(screen.getByText("3,66 т")).toBeInTheDocument();
    expect(screen.getByText("Вывоз · Снимок стабилен")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Весы «Вывоз»: 3,66 т, Снимок стабилен" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Весы «Вывоз»: 3,66 т, Снимок стабилен");
  });

  it("also refreshes immediately when the operator asks", async () => {
    const user = userEvent.setup();
    mockApi(preview());

    render(<LiveScaleStatus active scaleKey="truck" label="Вывоз" />);

    expect(mocks.reload).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Обновить весы «Вывоз»" }));
    expect(mocks.reload).toHaveBeenCalledOnce();
  });

  it("marks a changing reading as approximate", () => {
    mockApi(
      preview({
        state: "unstable",
        ready: false,
        capturable: false,
        stable: false,
      }),
    );

    render(<LiveScaleStatus active scaleKey="truck" label="Вывоз" />);

    expect(screen.getByText("≈ 3,66 т")).toBeInTheDocument();
    expect(screen.getByText("Вывоз · Снимок меняется")).toBeInTheDocument();
  });

  it.each([
    ["stale", "Нет свежих данных"],
    ["disconnected", "Весы отключены"],
    ["unavailable", "ПК весов недоступен"],
  ] as const)("does not show an old value when the scale is %s", (state, label) => {
    mockApi(
      preview({
        state,
        ready: false,
        capturable: false,
        connected: state !== "disconnected",
        stable: false,
        stale: state === "stale",
      }),
    );

    render(<LiveScaleStatus active scaleKey="truck" label="Вывоз" />);

    expect(screen.queryByText(/3,66 т/)).not.toBeInTheDocument();
    expect(screen.getByText("—,— т")).toBeInTheDocument();
    expect(screen.getByText(`Вывоз · ${label}`)).toBeInTheDocument();
  });

  it("hides a retained reading after a CRM network error", () => {
    mockApi(preview(), "Сеть недоступна");

    render(<LiveScaleStatus active scaleKey="truck" label="Вывоз" />);

    expect(screen.queryByText(/3,66 т/)).not.toBeInTheDocument();
    expect(screen.getByText("Вывоз · Нет связи с CRM")).toBeInTheDocument();
  });

  it("does not fetch or render without weighing permission", () => {
    mockApi(null);

    const { container } = render(<LiveScaleStatus active={false} scaleKey="truck" label="Вывоз" />);

    expect(mocks.useApi).toHaveBeenCalledWith(null);
    expect(container).toBeEmptyDOMElement();
  });
});
