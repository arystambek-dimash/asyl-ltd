import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import VehiclePlateEventsPage from "./page";

const getMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  apiError: () => "request failed",
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/use-debounced", () => ({ useDebounced: (value: string) => value }));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

const detectedEvent = {
  id: 123,
  event_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
  vehicle_number: "123ABC02",
  camera: "cam1",
  source: "main",
  detected_at: "2026-08-25T12:30:00.000Z",
  stationary_seconds: "3.4",
  confirmation_votes: 3,
  detector_confidence: "0.91",
  ocr_confidence: "0.96",
  processing_status: "received",
};

function page(results: (typeof detectedEvent)[]) {
  return { data: { count: results.length, next: null, previous: null, results } };
}

describe("VehiclePlateEventsPage", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("показывает журнал и передаёт фильтры в API", async () => {
    const user = userEvent.setup();
    getMock.mockResolvedValue(page([detectedEvent]));

    render(<VehiclePlateEventsPage />);

    expect(await screen.findByLabelText("Номер машины 123 ABC 02")).toBeInTheDocument();
    expect(screen.getByText("cam1")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("3,4 с")).toBeInTheDocument();
    expect(screen.getByText("96%")).toBeInTheDocument();
    expect(screen.getByText("Получено")).toBeInTheDocument();
    expect(getMock).toHaveBeenLastCalledWith("/vehicle-plate-events?page=1&page_size=100", expect.anything());

    await user.type(screen.getByLabelText("Номер машины"), "123 abc 02");
    await user.type(screen.getByLabelText("Камера"), "CAM1");
    await user.type(screen.getByLabelText("Дата с"), "2026-08-01");
    await user.type(screen.getByLabelText("Дата по"), "2026-08-25");

    await waitFor(() =>
      expect(getMock).toHaveBeenLastCalledWith(
        "/vehicle-plate-events?vehicle_number=123ABC02&date_from=2026-08-01&date_to=2026-08-25&camera=cam1&page=1&page_size=100",
        expect.anything(),
      ),
    );
  });

  it("показывает новое событие после polling без ручного обновления", async () => {
    vi.useFakeTimers();
    getMock.mockResolvedValueOnce(page([])).mockResolvedValueOnce(page([detectedEvent]));

    render(<VehiclePlateEventsPage />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("Событий пока нет")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(10_000));

    expect(screen.getByLabelText("Номер машины 123 ABC 02")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(2);
  });
});
