import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OrientationDatasetPage from "./page";
import type { GrainOrientationSample, GrainOrientationSummary, Me } from "@/lib/types";

const getMock = vi.hoisted(() => vi.fn());
const postMock = vi.hoisted(() => vi.fn());
const pollingMock = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ me: null as unknown }));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock, defaults: { baseURL: "https://crm.test/api" } },
  apiError: () => "Кадр уже удалён с ПК",
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number) => pollingMock(poll, intervalMs),
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: auth.me }),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({
    title,
    description,
    actions,
    children,
  }: {
    title: string;
    description?: string;
    actions?: ReactNode;
    children: ReactNode;
  }) => (
    <main>
      <h1>{title}</h1>
      {description && <p>{description}</p>}
      {actions}
      {children}
    </main>
  ),
}));

const admin = {
  id: 1,
  username: "admin",
  is_client: false,
  is_superuser: false,
  is_monoblock: false,
  permissions: ["grain.view", "grain.admin"],
} as Me;

const viewer = { ...admin, id: 2, username: "viewer", permissions: ["grain.view"] } as Me;

const tripSample: GrainOrientationSample = {
  id: 5,
  sample_id: "weighing-5",
  record_kind: "weighing",
  record_id: 5,
  label: "front",
  label_source: "trip",
  weight_kg: 8_760,
  captured_at: "2026-09-04T09:31:24Z",
  model_orientation: "front",
  conflict: false,
  excluded: false,
  sent_at: "2026-09-05T01:30:00Z",
  last_error: "",
  reviewed_by_name: null,
  reviewed_at: null,
  photo_url: "/api/grain/photos/weighing/5/?token=abc",
  vehicle_number: "465BDS13",
  wagon: 11,
};

const conflictSample: GrainOrientationSample = {
  ...tripSample,
  id: 6,
  sample_id: "unassigned-6",
  record_kind: "unassigned",
  record_id: 6,
  label_source: "weight",
  weight_kg: 4_100,
  model_orientation: "rear",
  conflict: true,
  sent_at: null,
  photo_url: "/api/grain/photos/unassigned/6/?token=def",
  vehicle_number: "",
  wagon: null,
};

const excludedSample: GrainOrientationSample = {
  ...tripSample,
  id: 7,
  sample_id: "weighing-7",
  record_id: 7,
  label: "rear",
  excluded: true,
  reviewed_by_name: "admin",
  reviewed_at: "2026-09-05T08:00:00Z",
  photo_url: null,
};

const summary: GrainOrientationSummary = {
  total: 12,
  by_label: { front: 7, rear: 5 },
  by_source: { trip: 9, weight: 2, manual: 1 },
  conflicts: 1,
  excluded: 1,
  unsent: 2,
  camera_pc: {
    enabled: true,
    model: { name: "vehicle-orientation.trained.pt" },
    dataset: { front: 40, rear: 33 },
    training: {
      status: "promoted",
      ran_at: "2026-09-05T02:30:00Z",
      promoted: true,
      samples: 73,
      baseline: { accuracy: 0.94 },
      candidate: { accuracy: 0.965 },
      reason: "",
      current_model: "vehicle-orientation.trained.pt",
    },
  },
};

function mockList(results: GrainOrientationSample[], summaryData: GrainOrientationSummary | null = summary) {
  getMock.mockImplementation((url: string) => {
    if (url.startsWith("/grain/orientation-samples/summary/")) {
      return summaryData
        ? Promise.resolve({ data: summaryData })
        : Promise.reject({ response: { status: 502, data: { detail: "ПК не отвечает" } } });
    }
    return Promise.resolve({ data: { count: results.length, next: null, previous: null, results } });
  });
}

function listCalls() {
  return getMock.mock.calls
    .map(([url]) => url as string)
    .filter((url) => !url.startsWith("/grain/orientation-samples/summary/"));
}

describe("OrientationDatasetPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    pollingMock.mockReset();
    auth.me = admin;
  });

  it("shows the summary, the Camera-PC report and the cards with badges and signed photo links", async () => {
    mockList([tripSample, conflictSample, excludedSample]);
    render(<OrientationDatasetPage />);

    const trip = await screen.findByRole("article", { name: "Кадр weighing-5" });
    expect(within(trip).getByText("Передом → заезд")).toBeInTheDocument();
    expect(within(trip).getByText("по рейсу")).toBeInTheDocument();
    expect(within(trip).getByText("8 760 кг")).toBeInTheDocument();
    expect(within(trip).getByText("465BDS13")).toBeInTheDocument();
    expect(within(trip).getByText("отправлен на ПК")).toBeInTheDocument();
    expect(within(trip).getByRole("link", { name: "Машина 465BDS13" })).toHaveAttribute(
      "href",
      "https://crm.test/api/grain/photos/weighing/5/?token=abc",
    );
    expect(within(trip).queryByText(/модель:/)).not.toBeInTheDocument();

    const conflict = screen.getByRole("article", { name: "Кадр unassigned-6" });
    expect(within(conflict).getByText("по весу")).toBeInTheDocument();
    expect(within(conflict).getByText("модель: задом")).toBeInTheDocument();
    expect(within(conflict).getByText("ждёт отправки")).toBeInTheDocument();
    expect(within(conflict).getByRole("link", { name: "Машина на весах" })).toHaveAttribute(
      "href",
      "https://crm.test/api/grain/photos/unassigned/6/?token=def",
    );

    const excluded = screen.getByRole("article", { name: "Кадр weighing-7" });
    expect(within(excluded).getByText("исключён")).toBeInTheDocument();
    expect(within(excluded).getByText("Задом → выезд")).toBeInTheDocument();
    expect(within(excluded).getByText("кадр недоступен")).toBeInTheDocument();
    expect(within(excluded).getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
    expect(within(excluded).queryByRole("button", { name: "Исключить" })).not.toBeInTheDocument();

    // Сводка CRM и отчёт ПК.
    expect(screen.getByText("Всего").nextElementSibling).toHaveTextContent("12");
    expect(screen.getByText("Конфликты", { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    expect(screen.getByText("Не отправлено").nextElementSibling).toHaveTextContent("2");
    const pc = screen.getByRole("region", { name: "ПК камер" });
    expect(within(pc).getByText("передом 40 · задом 33")).toBeInTheDocument();
    expect(within(pc).getByText("промотирована")).toBeInTheDocument();
    expect(within(pc).getByText("94% → 96,5%")).toBeInTheDocument();
    expect(within(pc).getByText("самообученная модель активна")).toBeInTheDocument();

    expect(listCalls()).toEqual(["/grain/orientation-samples/?page=1&page_size=48"]);
    expect(pollingMock).toHaveBeenCalledWith(expect.any(Function), 30_000);
  });

  it("relabels a frame and swaps the card for the returned row", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock.mockResolvedValue({
      data: {
        ...tripSample,
        label: "rear",
        label_source: "manual",
        reviewed_by_name: "admin",
        reviewed_at: "2026-09-05T09:00:00Z",
        sent_at: null,
      },
    });
    render(<OrientationDatasetPage />);

    const card = await screen.findByRole("article", { name: "Кадр weighing-5" });
    expect(within(card).getByRole("button", { name: "Передом" })).toBeDisabled();
    await user.click(within(card).getByRole("button", { name: "Задом" }));

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/5/label/", { label: "rear" });
    expect(await within(card).findByText("Задом → выезд")).toBeInTheDocument();
    expect(within(card).getByText("вручную")).toBeInTheDocument();
    expect(within(card).getByText(/проверил admin/)).toBeInTheDocument();
    expect(within(card).getByText("ждёт отправки")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Задом" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "Передом" })).toBeEnabled();
    expect(within(card).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("returns an excluded frame with its current label", async () => {
    const user = userEvent.setup();
    mockList([excludedSample]);
    postMock.mockResolvedValue({ data: { ...excludedSample, excluded: false, reviewed_at: "2026-09-05T09:30:00Z" } });
    render(<OrientationDatasetPage />);

    const card = await screen.findByRole("article", { name: "Кадр weighing-7" });
    await user.click(within(card).getByRole("button", { name: "Вернуть" }));

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/7/label/", { label: "rear" });
    await waitFor(() => expect(within(card).queryByText("исключён")).not.toBeInTheDocument());
    expect(within(card).getByRole("button", { name: "Исключить" })).toBeInTheDocument();
  });

  it("hides the actions from a viewer without grain.admin", async () => {
    auth.me = viewer;
    mockList([tripSample, excludedSample]);
    render(<OrientationDatasetPage />);

    await screen.findByRole("article", { name: "Кадр weighing-5" });
    expect(screen.queryByRole("button", { name: "Передом" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Задом" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Исключить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуть" })).not.toBeInTheDocument();
    expect(screen.getByText("Передом → заезд")).toBeInTheDocument();
  });

  it("maps the filters to query params and restarts from the first page", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const scopes = screen.getByRole("tablist", { name: "Фильтр кадров" });
    await user.click(within(scopes).getByRole("tab", { name: /^Конфликты/ }));
    await waitFor(() => expect(listCalls().at(-1)).toBe("/grain/orientation-samples/?conflict=1&page=1&page_size=48"));

    const labels = screen.getByRole("tablist", { name: "Метка" });
    await user.click(within(labels).getByRole("tab", { name: /^Задом/ }));
    await waitFor(() =>
      expect(listCalls().at(-1)).toBe("/grain/orientation-samples/?conflict=1&label=rear&page=1&page_size=48"),
    );

    await user.click(within(scopes).getByRole("tab", { name: /^Не отправлены/ }));
    await waitFor(() =>
      expect(listCalls().at(-1)).toBe("/grain/orientation-samples/?unsent=1&label=rear&page=1&page_size=48"),
    );

    await user.click(within(scopes).getByRole("tab", { name: /^По весу/ }));
    await waitFor(() =>
      expect(listCalls().at(-1)).toBe("/grain/orientation-samples/?source=weight&label=rear&page=1&page_size=48"),
    );
  });

  it("keeps a failed action's error inside the card", async () => {
    const user = userEvent.setup();
    mockList([tripSample, conflictSample]);
    postMock.mockRejectedValueOnce({ response: { status: 409, data: { detail: "gone", code: "sample_gone" } } });
    render(<OrientationDatasetPage />);

    const card = await screen.findByRole("article", { name: "Кадр weighing-5" });
    await user.click(within(card).getByRole("button", { name: "Исключить" }));

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/5/exclude/", {});
    expect(await within(card).findByRole("alert")).toHaveTextContent("Кадр уже удалён с ПК");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(within(card).getByText("Передом → заезд")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Исключить" })).toBeEnabled();
  });

  it("tells when the Camera-PC is unreachable and refreshes on demand", async () => {
    const user = userEvent.setup();
    mockList([tripSample], { ...summary, camera_pc: null });
    render(<OrientationDatasetPage />);

    await screen.findByRole("article", { name: "Кадр weighing-5" });
    expect(screen.getByText("ПК камер недоступен")).toBeInTheDocument();

    const before = getMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(getMock.mock.calls.length).toBe(before + 2));
  });
});
