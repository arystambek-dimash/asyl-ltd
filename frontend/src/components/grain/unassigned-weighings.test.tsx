import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UnassignedWeighingsPanel } from "./unassigned-weighings";
import type { GrainUnassignedWeighing, GrainWagon } from "@/lib/types";

const postMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());
const pollingMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { post: postMock, defaults: { baseURL: "https://crm.test/api" } },
  apiError: () => "Рейс сейчас не ждёт взвешивания",
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string | null) => useApiMock(url),
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number, active?: boolean) =>
    pollingMock(poll, intervalMs, active),
}));

const item: GrainUnassignedWeighing = {
  id: 5,
  weight_kg: 30_010,
  stable_weight_at: "2026-09-04T09:31:24Z",
  scale_number: "truck",
  camera: "cam1",
  photo_url: "/api/grain/photos/unassigned/5/?token=abc",
  reason: "open_passages_exist",
  status: "open",
  wagon: null,
  wagon_number: "",
  action: "",
  resolved_by_name: null,
  resolved_at: null,
  created_at: "2026-09-04T09:31:30Z",
};

const loaded = {
  id: 11,
  number: "465BDS13",
  direction: "passage",
  status: "at_silo",
  status_label: "На территории · погрузка",
  entry_weight_kg: 12_000,
  exit_weight_kg: null,
} as GrainWagon;

const finished = {
  id: 12,
  number: "506WKZ13",
  direction: "passage",
  status: "completed",
  status_label: "Завершён",
  entry_weight_kg: 12_000,
  exit_weight_kg: 30_000,
} as GrainWagon;

function mockApi(items: unknown, candidates: unknown) {
  useApiMock.mockImplementation((url: string | null) => ({
    data: url?.startsWith("/grain/unassigned-weighings/") ? items : candidates,
    loading: false,
    error: "",
    reload: vi.fn().mockResolvedValue(undefined),
  }));
}

describe("UnassignedWeighingsPanel", () => {
  beforeEach(() => {
    postMock.mockReset();
    useApiMock.mockReset();
    pollingMock.mockReset();
  });

  it("renders nothing while the queue is empty or the response is not a queue", () => {
    mockApi([], []);
    const { container, rerender } = render(<UnassignedWeighingsPanel canWeigh />);
    expect(container).toBeEmptyDOMElement();

    mockApi({ results: [{ id: 1 }] }, []);
    rerender(<UnassignedWeighingsPanel canWeigh />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the parked weight, its photo link, a likely exit and only waiting passages as targets", async () => {
    mockApi([item], [loaded, finished]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getByText("Неопознанные взвешивания")).toBeInTheDocument();
    expect(screen.getByText("30 010 кг")).toBeInTheDocument();
    expect(screen.getByText(/похоже на выезд 465BDS13/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Машина на весах" })).toHaveAttribute(
      "href",
      "https://crm.test/api/grain/photos/unassigned/5/?token=abc",
    );

    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));
    const select = screen.getByLabelText("Рейс для привязки") as HTMLSelectElement;
    const labels = Array.from(select.options).map((option) => option.textContent);
    expect(labels).toHaveLength(2);
    expect(labels[0]).toBe("Выберите рейс…");
    expect(labels[1]).toMatch(/^465BDS13 · ждёт вес гружёной/);
    // A loaded weight preselects the most likely exit.
    expect(select.value).toBe("11");
  });

  it("binds the weight to the chosen passage and refreshes the caller", async () => {
    mockApi([item], [loaded]);
    postMock.mockResolvedValue({ data: {} });
    const onChanged = vi.fn();
    render(<UnassignedWeighingsPanel canWeigh onChanged={onChanged} />);

    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));
    await userEvent.selectOptions(screen.getByLabelText("Рейс для привязки"), "11");
    await userEvent.click(screen.getByRole("button", { name: /^Привязать$/ }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(postMock).toHaveBeenCalledWith("/grain/unassigned-weighings/5/assign/", { wagon: 11 });
  });

  it("keeps a failed action's error inside the row", async () => {
    mockApi([item], [loaded]);
    postMock.mockRejectedValueOnce({ response: { data: { code: "wagon_not_awaiting_weight" } } });
    render(<UnassignedWeighingsPanel canWeigh />);

    await userEvent.click(screen.getByRole("button", { name: "Отклонить взвешивание" }));
    await userEvent.click(screen.getByRole("button", { name: /^Отклонить$/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Рейс сейчас не ждёт взвешивания");
    expect(postMock).toHaveBeenCalledWith("/grain/unassigned-weighings/5/discard/", { reason: "" });
  });

  it("hides the actions from viewers without weigh permission", () => {
    mockApi([item], [loaded]);
    render(<UnassignedWeighingsPanel canWeigh={false} />);

    expect(screen.queryByRole("button", { name: "Привязать" })).not.toBeInTheDocument();
    expect(screen.getByText(/похоже на выезд/)).toBeInTheDocument();
  });

  it("suggests a new trip for an empty truck", () => {
    mockApi([{ ...item, id: 6, weight_kg: 3_900 }], [loaded]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getByText("3 900 кг")).toBeInTheDocument();
    expect(screen.getByText(/похоже на новый заезд/)).toBeInTheDocument();
  });

  it("collapses a long queue to the latest rows until expanded", async () => {
    const many = Array.from({ length: 5 }, (_value, index) => ({ ...item, id: 100 + index }));
    mockApi(many, [loaded]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getAllByText("30 010 кг")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: /Показать ещё 2/ }));
    expect(screen.getAllByText("30 010 кг")).toHaveLength(5);
    await userEvent.click(screen.getByRole("button", { name: /Свернуть/ }));
    expect(screen.getAllByText("30 010 кг")).toHaveLength(3);
  });
});
