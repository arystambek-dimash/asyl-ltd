import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { apiState } from "@/test-utils/api";
import { PassageHistory } from "./passage-history";

const useApiMock = vi.hoisted(() => vi.fn());
const pollingMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/use-api", () => ({ useApi: (url: string | null) => useApiMock(url) }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: (...args: unknown[]) => pollingMock(...args) }));

beforeEach(() => {
  useApiMock.mockReset();
  pollingMock.mockReset();
  useApiMock.mockReturnValue(apiState(null));
});

it("loads the selected journal and pauses live polling on older pages", async () => {
  useApiMock.mockImplementation((url: string | null) => apiState(url ? { results: [], next_cursor: 42 } : null));
  render(<PassageHistory />);
  expect(useApiMock).toHaveBeenLastCalledWith("/grain/automatic-passage-scale/history/");
  expect(pollingMock.mock.lastCall?.[2]).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Более ранние" }));
  expect(useApiMock).toHaveBeenLastCalledWith("/grain/automatic-passage-scale/history/?before=42");
  expect(pollingMock.mock.lastCall?.[2]).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Последние" }));
  expect(useApiMock).toHaveBeenLastCalledWith("/grain/automatic-passage-scale/history/");
  expect(pollingMock.mock.lastCall?.[2]).toBe(true);
});

it("refreshes the journal on demand", async () => {
  const reload = vi.fn();
  useApiMock.mockReturnValue(apiState({ results: [], next_cursor: null }, { reload }));
  render(<PassageHistory />);
  await userEvent.click(screen.getByRole("button", { name: "Обновить журнал" }));
  expect(reload).toHaveBeenCalledOnce();
});

it("shows a failed stabilization separately from a saved weight awaiting assignment", async () => {
  useApiMock.mockReturnValue({
    data: {
      results: [
        {
          id: 2,
          occurred_at: "2026-09-08T08:00:00Z",
          weight_kg: null,
          vehicle_number: "",
          status: "failed",
          reason: "automatic_scale_not_stable",
          photo_status: "unavailable",
        },
        {
          id: 1,
          occurred_at: "2026-09-08T07:00:00Z",
          weight_kg: 8560,
          vehicle_number: "934PRB13",
          status: "completed",
          action: "unassigned",
          reason: "entry_missing",
          photo_status: "retrying",
        },
      ],
      next_cursor: null,
    },
    loading: false,
    error: "",
    reload: vi.fn(),
  });
  render(<PassageHistory />);
  expect(screen.getByText("Вес не подтверждён")).toBeInTheDocument();
  expect(screen.getByText(/Машина съехала до подтверждения/)).toBeInTheDocument();
  expect(screen.getByText(/Нужна привязка/)).toBeInTheDocument();
  expect(screen.getByText("Фото загружается повторно")).toBeInTheDocument();
});

it("does not show a loading error as an empty history", async () => {
  useApiMock.mockReturnValue(apiState(null, { error: "offline" }));
  render(<PassageHistory />);
  expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить журнал");
  expect(screen.queryByText("Попыток взвешивания пока нет.")).not.toBeInTheDocument();
});

it("distinguishes automatic resolution from operator action without showing stale failure reasons", async () => {
  useApiMock.mockReturnValue({
    data: {
      results: ["automatic", "manual", "discarded"].map((resolution, index) => ({
        id: index + 20,
        occurred_at: "2026-08-01T08:00:00Z",
        weight_kg: 9100,
        vehicle_number: "314XYZ01",
        status: "completed",
        action: "unassigned",
        reason: "entry_missing",
        resolved: true,
        resolution,
        photo_status: "saved",
      })),
      next_cursor: null,
    },
    loading: false,
    error: "",
    reload: vi.fn(),
  });
  render(<PassageHistory />);
  expect(screen.getByText("Оформлено автоматически")).toBeInTheDocument();
  expect(screen.getByText("Обработано оператором")).toBeInTheDocument();
  expect(screen.getByText("Отклонено")).toBeInTheDocument();
  expect(screen.queryByText(/выезд без заезда/)).not.toBeInTheDocument();
});
