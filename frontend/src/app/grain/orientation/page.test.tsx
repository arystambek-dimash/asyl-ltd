import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OrientationDatasetPage from "./page";
import type { GrainOrientationPurgeResult, GrainOrientationSample, GrainOrientationSummary, Me } from "@/lib/types";

const getMock = vi.hoisted(() => vi.fn());
const postMock = vi.hoisted(() => vi.fn());
const pollingMock = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ me: null as unknown, loading: false }));

vi.mock("@/lib/api", () => ({
  api: { get: getMock, post: postMock, defaults: { baseURL: "https://crm.test/api" } },
  apiError: () => "Кадр уже удалён с ПК",
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number) => pollingMock(poll, intervalMs),
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: auth.me, loading: auth.loading }),
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

/** Страница только для владельца: права grain.* роли не играют. */
const owner = {
  id: 1,
  username: "owner",
  is_client: false,
  is_superuser: true,
  is_monoblock: false,
  permissions: [] as string[],
} as Me;

const admin = {
  ...owner,
  id: 2,
  username: "admin",
  is_superuser: false,
  permissions: ["grain.view", "grain.admin"],
} as Me;

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

async function openPurgeDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Очистить датасет…" }));
  return screen.findByRole("dialog", { name: "Очистить датасет?" });
}

/** Последний пакет чистки: бэкенд больше ничего не нашёл под фильтр. */
function purgeBatch(deleted: number, removedFromPc: number, remaining = 0): GrainOrientationPurgeResult {
  return { deleted, removed_from_pc: removedFromPc, pc_unavailable: false, remaining };
}

describe("OrientationDatasetPage", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    pollingMock.mockReset();
    auth.me = owner;
    auth.loading = false;
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
        reviewed_by_name: "owner",
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
    expect(within(card).getByText(/проверил owner/)).toBeInTheDocument();
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

  it("shows the loading placeholder while the session is being read", () => {
    auth.me = null;
    auth.loading = true;
    mockList([tripSample]);
    render(<OrientationDatasetPage />);

    expect(screen.getByRole("heading", { name: "Датасет ориентации" })).toBeInTheDocument();
    expect(screen.getByText("Загрузка…")).toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
  });

  it("denies a staff user with grain.admin who is not a superuser and never requests the dataset", async () => {
    auth.me = admin;
    mockList([tripSample]);
    render(<OrientationDatasetPage />);

    expect(await screen.findByText("Нет доступа")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Датасет ориентации" })).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Очистить датасет…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Обновить" })).not.toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
    expect(pollingMock).not.toHaveBeenCalled();
  });

  it("lets the superuser edit labels and purge the dataset", async () => {
    mockList([tripSample, excludedSample]);
    render(<OrientationDatasetPage />);

    const card = await screen.findByRole("article", { name: "Кадр weighing-5" });
    expect(within(card).getByRole("button", { name: "Передом" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Задом" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Исключить" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Очистить датасет…" })).toBeInTheDocument();
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

  it("purges every frame, shows the result inside the dialog and refreshes the page", async () => {
    const user = userEvent.setup();
    mockList([tripSample, conflictSample]);
    postMock.mockResolvedValue({ data: purgeBatch(12, 10) });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    // По умолчанию — безопасный вариант «старше 30 дней»; фокус на первом поле.
    expect(within(dialog).getByLabelText("Какие кадры")).toHaveValue("older");
    expect(within(dialog).getByLabelText("Какие кадры")).toHaveFocus();
    expect(within(dialog).getByLabelText("Старше, дней")).toHaveValue(30);

    await user.selectOptions(within(dialog).getByLabelText("Какие кадры"), "all");
    expect(within(dialog).queryByLabelText("Старше, дней")).not.toBeInTheDocument();
    const before = getMock.mock.calls.length;
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/purge/", { older_than_days: null });
    const result = await within(dialog).findByRole("status");
    expect(within(result).getByText("Удалено из CRM").nextElementSibling).toHaveTextContent("12");
    expect(within(result).getByText("Удалено с ПК").nextElementSibling).toHaveTextContent("10");
    expect(within(result).queryByText(/ПК камер недоступен/)).not.toBeInTheDocument();
    expect(within(result).queryByText(/повторите очистку/)).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Удалить кадры" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    // Пакет был последним — второго запроса нет; список и сводка перечитаны один раз.
    expect(postMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(getMock.mock.calls.length).toBe(before + 2));

    await user.click(within(dialog).getByRole("button", { name: "Готово" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("repeats the purge while batches remain, showing live progress, and sums the counters", async () => {
    const user = userEvent.setup();
    mockList([tripSample, conflictSample]);
    let finishSecondBatch!: (value: { data: GrainOrientationPurgeResult }) => void;
    postMock
      .mockResolvedValueOnce({ data: purgeBatch(5, 4, 3) })
      .mockImplementationOnce(() => new Promise((resolve) => (finishSecondBatch = resolve)));
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    const before = getMock.mock.calls.length;
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));

    // Между пакетами — живой счётчик, кнопки заблокированы, страница ещё не перечитана.
    expect(await within(dialog).findByRole("status")).toHaveTextContent("Удалено 5 кадров…");
    expect(within(dialog).getByRole("button", { name: "Удаление…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Отмена" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Какие кадры")).toBeDisabled();
    expect(postMock).toHaveBeenCalledTimes(2);
    expect(getMock.mock.calls.length).toBe(before);

    await act(async () => finishSecondBatch({ data: purgeBatch(3, 3) }));

    const result = await within(dialog).findByText("Удалено из CRM");
    expect(result.nextElementSibling).toHaveTextContent("8");
    expect(within(dialog).getByText("Удалено с ПК").nextElementSibling).toHaveTextContent("7");
    expect(postMock).toHaveBeenCalledTimes(2);
    expect(postMock.mock.calls.map(([, body]) => body)).toEqual([{ older_than_days: 30 }, { older_than_days: 30 }]);
    expect(within(dialog).getByRole("button", { name: "Готово" })).toBeInTheDocument();
    await waitFor(() => expect(getMock.mock.calls.length).toBe(before + 2));
  });

  it("stops after 100 batches and asks to run the purge again", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock.mockResolvedValue({ data: purgeBatch(2, 2, 1) });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));

    expect(await within(dialog).findByText("Удалено из CRM")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledTimes(100);
    expect(within(dialog).getByText("Удалено из CRM").nextElementSibling).toHaveTextContent("200");
    expect(within(dialog).getByText("Осталось ещё 1 кадров — повторите очистку.")).toBeInTheDocument();
  });

  it("purges frames older than N days and stops on the first batch without the Camera-PC", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock.mockResolvedValue({ data: { deleted: 3, removed_from_pc: 0, pc_unavailable: true, remaining: 4 } });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    const days = within(dialog).getByLabelText("Старше, дней");
    const confirm = within(dialog).getByRole("button", { name: "Удалить кадры" });
    expect(days).toHaveAttribute("max", "3650");
    await user.clear(days);
    expect(confirm).toBeDisabled();
    await user.type(days, "3651");
    expect(confirm).toBeDisabled();
    await user.clear(days);
    await user.type(days, "3650");
    expect(confirm).toBeEnabled();
    await user.clear(days);
    await user.type(days, "7");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/purge/", { older_than_days: 7 });
    const result = await within(dialog).findByRole("status");
    expect(within(result).getByText("Удалено из CRM").nextElementSibling).toHaveTextContent("3");
    expect(
      within(result).getByText(
        "ПК камер недоступен: кадры, уже отправленные на ПК, остались в CRM как исключённые. Повторите очистку, когда ПК будет доступен",
      ),
    ).toBeInTheDocument();
    expect(within(result).queryByText(/Осталось ещё/)).not.toBeInTheDocument();
    // Без ПК следующий пакет оставил бы те же строки — цикл прерван, хотя remaining > 0.
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("opens a fresh dialog every time: earlier choices and results are not carried over", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock.mockResolvedValue({ data: purgeBatch(1, 1) });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    let dialog = await openPurgeDialog(user);
    await user.selectOptions(within(dialog).getByLabelText("Какие кадры"), "all");
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));
    await within(dialog).findByRole("button", { name: "Готово" });
    await user.click(within(dialog).getByRole("button", { name: "Готово" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    dialog = await openPurgeDialog(user);
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Какие кадры")).toHaveValue("older");
    expect(within(dialog).getByLabelText("Какие кадры")).toHaveFocus();
    expect(within(dialog).getByLabelText("Старше, дней")).toHaveValue(30);
    expect(within(dialog).getByRole("button", { name: "Удалить кадры" })).toBeEnabled();

    // Закрытие без чистки тоже не оставляет следов.
    await user.selectOptions(within(dialog).getByLabelText("Какие кадры"), "all");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    dialog = await openPurgeDialog(user);
    expect(within(dialog).getByLabelText("Какие кадры")).toHaveValue("older");
  });

  it("keeps the counter and the error inside the dialog when a later batch fails", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock
      .mockResolvedValueOnce({ data: purgeBatch(5, 5, 2) })
      .mockRejectedValueOnce({ response: { status: 502, data: { detail: "ПК не отвечает" } } })
      .mockResolvedValueOnce({ data: purgeBatch(2, 2) });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    const before = getMock.mock.calls.length;
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Кадр уже удалён с ПК");
    expect(within(dialog).getByRole("status")).toHaveTextContent("Удалено 5 кадров");
    expect(within(dialog).queryByText("Удалено из CRM")).not.toBeInTheDocument();
    const retry = within(dialog).getByRole("button", { name: "Удалить кадры" });
    expect(retry).toBeEnabled();
    // Первый пакет что-то удалил — страница перечитана, несмотря на ошибку.
    await waitFor(() => expect(getMock.mock.calls.length).toBe(before + 2));

    await user.click(retry);
    expect(await within(dialog).findByText("Удалено из CRM")).toBeInTheDocument();
    expect(within(dialog).getByText("Удалено из CRM").nextElementSibling).toHaveTextContent("7");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(postMock).toHaveBeenCalledTimes(3);
  });

  it("keeps a failed purge's error inside the dialog and lets the owner retry", async () => {
    const user = userEvent.setup();
    mockList([tripSample]);
    postMock.mockRejectedValueOnce({ response: { status: 502, data: { detail: "ПК не отвечает" } } });
    render(<OrientationDatasetPage />);
    await screen.findByRole("article", { name: "Кадр weighing-5" });

    const dialog = await openPurgeDialog(user);
    const before = getMock.mock.calls.length;
    await user.click(within(dialog).getByRole("button", { name: "Удалить кадры" }));

    expect(postMock).toHaveBeenCalledWith("/grain/orientation-samples/purge/", { older_than_days: 30 });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Кадр уже удалён с ПК");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(within(dialog).queryByRole("status")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Удалить кадры" })).toBeEnabled();
    expect(getMock.mock.calls.length).toBe(before);
  });
});
