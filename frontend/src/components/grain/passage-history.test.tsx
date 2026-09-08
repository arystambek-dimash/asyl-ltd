import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { PassageHistory } from "./passage-history";
import { WagonPhotos } from "./wagon-photos";
import type { GrainWagon } from "@/lib/types";

const useApiMock = vi.hoisted(() => vi.fn());
const pollingMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/use-api", () => ({ useApi: (url: string | null) => useApiMock(url) }));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: (...args: unknown[]) => pollingMock(...args) }));

beforeEach(() => {
  useApiMock.mockReset();
  pollingMock.mockReset();
  useApiMock.mockReturnValue({ data: null, loading: false, error: "", reload: vi.fn() });
});

it("requests history only after opening and pauses live polling on older pages", async () => {
  useApiMock.mockImplementation((url: string | null) => ({
    data: url ? { results: [], next_cursor: 42 } : null,
    loading: false,
    error: "",
    reload: vi.fn(),
  }));
  render(<PassageHistory />);
  expect(useApiMock).toHaveBeenLastCalledWith(null);
  await userEvent.click(screen.getByRole("button", { name: "Журнал взвешиваний" }));
  expect(useApiMock).toHaveBeenLastCalledWith("/grain/automatic-passage-scale/history/");
  await userEvent.click(screen.getByRole("button", { name: "Более ранние" }));
  expect(useApiMock).toHaveBeenLastCalledWith("/grain/automatic-passage-scale/history/?before=42");
  expect(pollingMock.mock.lastCall?.[2]).toBe(false);
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
  await userEvent.click(screen.getByRole("button", { name: "Журнал взвешиваний" }));
  expect(screen.getByText("Вес не подтверждён")).toBeInTheDocument();
  expect(screen.getByText(/Машина съехала до подтверждения/)).toBeInTheDocument();
  expect(screen.getByText(/Нужна привязка/)).toBeInTheDocument();
  expect(screen.getByText("Фото загружается повторно")).toBeInTheDocument();
});

it("does not show a loading error as an empty history", async () => {
  useApiMock.mockReturnValue({ data: null, loading: false, error: "offline", reload: vi.fn() });
  render(<PassageHistory />);
  await userEvent.click(screen.getByRole("button", { name: "Журнал взвешиваний" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить журнал");
  expect(screen.queryByText("Попыток взвешивания пока нет.")).not.toBeInTheDocument();
});

it("distinguishes an expected exit photo from a retrying entry photo", () => {
  render(
    <WagonPhotos
      wagon={
        {
          direction: "passage",
          entry_weight_kg: 3900,
          exit_weight_kg: null,
          weighings: [{ id: 1, kind: "gross", photo_status: "retrying" }],
        } as GrainWagon
      }
    />,
  );
  expect(screen.getByText("Фото загружается повторно")).toBeInTheDocument();
  expect(screen.getByText("появится после взвешивания гружёной")).toBeInTheDocument();
});
