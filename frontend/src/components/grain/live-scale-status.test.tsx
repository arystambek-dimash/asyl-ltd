import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TruckScalePreview } from "@/lib/types";
import { LiveScaleStatus } from "./live-scale-status";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  useVisiblePolling: vi.fn(),
  reload: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.useVisiblePolling }));

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
    poll_after_ms: 2400,
    ...overrides,
  };
}

function mockApi(data: TruckScalePreview | null, error = "", loading = false) {
  mocks.useApi.mockReturnValue({ data, error, loading, reload: mocks.reload });
}

describe("LiveScaleStatus", () => {
  beforeEach(() => {
    mocks.useApi.mockReset();
    mocks.useVisiblePolling.mockReset();
    mocks.reload.mockReset();
  });

  it("shows the current stable weight in tonnes and uses the server polling interval", () => {
    mockApi(preview());

    render(<LiveScaleStatus active />);

    expect(mocks.useApi).toHaveBeenCalledWith("/truck-scale/reading/");
    expect(mocks.useVisiblePolling).toHaveBeenCalledWith(mocks.reload, 2400, true);
    expect(screen.getByText("3,66 т")).toBeInTheDocument();
    expect(screen.getByText("Вес стабилен")).toBeInTheDocument();
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

    render(<LiveScaleStatus active />);

    expect(screen.getByText("≈ 3,66 т")).toBeInTheDocument();
    expect(screen.getByText("Вес меняется")).toBeInTheDocument();
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

    render(<LiveScaleStatus active />);

    expect(screen.queryByText(/3,66 т/)).not.toBeInTheDocument();
    expect(screen.getByText("—,— т")).toBeInTheDocument();
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("hides a retained reading after a CRM network error and slows polling", () => {
    mockApi(preview(), "Сеть недоступна");

    render(<LiveScaleStatus active />);

    expect(screen.queryByText(/3,66 т/)).not.toBeInTheDocument();
    expect(screen.getByText("Нет связи с CRM")).toBeInTheDocument();
    expect(mocks.useVisiblePolling).toHaveBeenCalledWith(mocks.reload, 5000, true);
  });

  it("does not fetch, poll, or render without weighing permission", () => {
    mockApi(null);

    const { container } = render(<LiveScaleStatus active={false} />);

    expect(mocks.useApi).toHaveBeenCalledWith(null);
    expect(mocks.useVisiblePolling).toHaveBeenCalledWith(mocks.reload, 2000, false);
    expect(container).toBeEmptyDOMElement();
  });
});
