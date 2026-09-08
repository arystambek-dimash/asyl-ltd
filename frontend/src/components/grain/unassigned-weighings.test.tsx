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
  vehicle_number: "",
  orientation: "",
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

  it("distinguishes an empty queue from an invalid response", () => {
    mockApi([], []);
    const { container, rerender } = render(<UnassignedWeighingsPanel canWeigh />);
    expect(container).toBeEmptyDOMElement();

    mockApi({ results: [{ id: 1 }] }, []);
    rerender(<UnassignedWeighingsPanel canWeigh />);
    expect(screen.getByRole("alert")).toHaveTextContent("некорректный список");
  });

  it("shows queue failures and retries both the queue and candidate passages", async () => {
    const reloadQueue = vi.fn().mockResolvedValue(undefined);
    const reloadCandidates = vi.fn().mockResolvedValue(undefined);
    useApiMock.mockImplementation((url: string) => ({
      data: null,
      loading: false,
      error: url.startsWith("/grain/unassigned-weighings/") ? "Весы недоступны" : "",
      reload: url.startsWith("/grain/unassigned-weighings/") ? reloadQueue : reloadCandidates,
    }));
    render(<UnassignedWeighingsPanel canWeigh />);
    expect(screen.getByRole("alert")).toHaveTextContent("Весы недоступны");
    await userEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
    expect(reloadQueue).toHaveBeenCalledTimes(1);
    expect(reloadCandidates).toHaveBeenCalledTimes(1);
  });

  it("refreshes candidate passages along with every queue poll", async () => {
    const reloadQueue = vi.fn().mockResolvedValue(undefined);
    const reloadCandidates = vi.fn().mockResolvedValue(undefined);
    useApiMock.mockImplementation((url: string) => ({
      data: [],
      loading: false,
      error: "",
      reload: url.startsWith("/grain/unassigned-weighings/") ? reloadQueue : reloadCandidates,
    }));
    render(<UnassignedWeighingsPanel canWeigh />);
    await pollingMock.mock.calls[0][0]();
    expect(reloadQueue).toHaveBeenCalledTimes(1);
    expect(reloadCandidates).toHaveBeenCalledTimes(1);
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

  it("requires choosing a trip when several trucks could have produced the exit weight", async () => {
    mockApi([item], [loaded, { ...loaded, id: 13, number: "996BKC13", entry_weight_kg: 3_980 }]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.queryByText(/похоже на выезд 465BDS13/)).not.toBeInTheDocument();
    expect(screen.getByText("номер не распознан — выберите рейс по фото и времени")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));
    expect(screen.getByLabelText("Рейс для привязки")).toHaveValue("");
    expect(screen.getByRole("button", { name: /^Привязать$/ })).toBeDisabled();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("assigns a saved rear weight to the open trip only after reviewing its weight and net", async () => {
    const exitWagon = { ...loaded, number: "996BKC13", entry_weight_kg: 3_980 };
    mockApi([{ ...item, orientation: "rear", weight_kg: 8_900 }], []);
    postMock.mockResolvedValue({ data: { action: "exit" } });
    const onChanged = vi.fn();
    const onBusyChange = vi.fn();
    render(
      <UnassignedWeighingsPanel canWeigh exitWagon={exitWagon} onChanged={onChanged} onBusyChange={onBusyChange} />,
    );

    expect(screen.getByRole("region", { name: "Выезд без распознанного номера" })).toBeInTheDocument();
    expect(useApiMock).not.toHaveBeenCalledWith("/grain/passages/?scope=on_site");
    expect(screen.queryByRole("button", { name: "Новый рейс" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отклонить взвешивание" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Выбрать этот вес" }));
    expect(postMock).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Рейс для привязки")).not.toBeInTheDocument();
    expect(screen.getByText(/Вывоз 996BKC13: вес гружёной 8 900 кг, нетто 4 920 кг/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Записать выезд и завершить рейс" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(postMock).toHaveBeenCalledExactlyOnceWith("/grain/unassigned-weighings/5/assign/", { wagon: 11 });
    expect(onBusyChange.mock.calls).toEqual([[true], [false]]);
  });

  it("offers only heavier exit weights captured after this trip's entry", () => {
    const exitWagon = {
      ...loaded,
      entry_weight_kg: 3_980,
      arrived_at: "2026-09-04T08:00:00Z",
      silo_arrived_at: "2026-09-04T09:00:00Z",
    };
    mockApi(
      [
        { ...item, id: 1, orientation: "front", weight_kg: 8_100 },
        { ...item, id: 2, orientation: "rear", weight_kg: 3_900 },
        { ...item, id: 3, orientation: "rear", weight_kg: 8_200, stable_weight_at: "2026-09-04T08:30:00Z" },
        { ...item, id: 4, orientation: "", weight_kg: 5_900 },
      ],
      [],
    );
    render(<UnassignedWeighingsPanel canWeigh exitWagon={exitWagon} />);

    expect(screen.getByText("5 900 кг")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("возможный выезд")).toBeInTheDocument();
  });

  it("blocks assignment if permission disappears while the confirmation is open", async () => {
    mockApi([item], []);
    const { rerender } = render(<UnassignedWeighingsPanel canWeigh exitWagon={loaded} />);
    await userEvent.click(screen.getByRole("button", { name: "Выбрать этот вес" }));
    rerender(<UnassignedWeighingsPanel canWeigh={false} exitWagon={loaded} />);

    expect(screen.getByRole("button", { name: "Записать выезд и завершить рейс" })).toBeDisabled();
    expect(postMock).not.toHaveBeenCalled();
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

describe("UnassignedWeighingsPanel camera orientation", () => {
  beforeEach(() => {
    postMock.mockReset();
    useApiMock.mockReset();
    pollingMock.mockReset();
  });

  it("trusts the camera over the weight: a light rear-facing truck is an exit", () => {
    mockApi([{ ...item, id: 7, weight_kg: 5_000, orientation: "rear" }], [loaded]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getByText("возможный выезд")).toBeInTheDocument();
    expect(screen.queryByText(/похоже на выезд 465BDS13/)).not.toBeInTheDocument();
    expect(screen.getByText(/камера: задом → выезд/)).toBeInTheDocument();
  });

  it("shows the plate of an exit without an entry and prefills it for a new trip", async () => {
    mockApi(
      [{ ...item, id: 8, weight_kg: 8_760, orientation: "rear", reason: "entry_missing", vehicle_number: "854ANB13" }],
      [loaded],
    );
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getByText("854ANB13")).toBeInTheDocument();
    expect(screen.getByText(/выезд без заезда/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Новый рейс" }));
    expect(screen.getByLabelText("Номер машины")).toHaveValue("854ANB13");
  });

  it("marks a front-facing truck as a new entry regardless of weight", () => {
    mockApi([{ ...item, id: 9, weight_kg: 8_000, orientation: "front" }], [loaded]);
    render(<UnassignedWeighingsPanel canWeigh />);

    expect(screen.getByText("возможный заезд")).toBeInTheDocument();
    expect(screen.getByText(/похоже на новый заезд/)).toBeInTheDocument();
    expect(screen.getByText(/камера: передом → заезд/)).toBeInTheDocument();
  });
});
