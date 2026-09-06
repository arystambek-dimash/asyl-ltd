import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WagonTable } from "./wagon-table";
import type { GrainWagon, Me } from "@/lib/types";

const deleteMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { delete: deleteMock },
  apiError: () => "Ошибка удаления",
}));
vi.mock("@/lib/use-local-day", () => ({ useLocalDay: () => "2026-09-06" }));

const me = {
  id: 1,
  username: "gate",
  permissions: ["grain.weigh"],
  is_superuser: false,
} as unknown as Me;

const admin = {
  id: 2,
  username: "boss",
  permissions: ["grain.weigh", "grain.delete"],
  is_superuser: false,
} as unknown as Me;

const finishedIntake = {
  id: 9,
  number: "Поезд-9",
  supplier: "ТОО Колос",
  status: "completed",
  status_label: "Завершён",
  net_weight_kg: 50_000,
} as const;

function wagon(overrides: Partial<GrainWagon>): GrainWagon {
  return {
    id: 1,
    supply: null,
    number: "",
    number_source: "manual",
    workflow: "simple",
    direction: "intake",
    cargo_name: "",
    status: "arrived",
    status_label: "Прибыл",
    unplanned: false,
    supplier: "",
    culture: "",
    grain_class: "",
    grain_type: null,
    grain_type_name: "",
    document_weight_kg: null,
    expected_weight_kg: null,
    arrived_at: null,
    gross_weight_kg: null,
    tare_weight_kg: null,
    net_weight_kg: null,
    entry_weight_kg: null,
    exit_weight_kg: null,
    weight_difference_kg: null,
    weight_difference_percent: null,
    weight_matches: null,
    assigned_silo: null,
    assigned_silo_name: null,
    ...overrides,
  } as GrainWagon;
}

function renderTable(wagons: GrainWagon[]) {
  render(<WagonTable wagons={wagons} me={me} emptyText="Пусто" />);
}

describe("WagonTable", () => {
  it("splits rows into «Приход» and «Вывоз» groups", () => {
    renderTable([
      wagon({ id: 1, number: "Поезд-1", supplier: "ТОО Колос" }),
      wagon({ id: 2, number: "123 ABC", direction: "passage", cargo_name: "Отруби" }),
    ]);

    expect(screen.getByText("Приход")).toBeInTheDocument();
    expect(screen.getByText("Вывоз")).toBeInTheDocument();
    // Подпись группы объясняет, что означают одни и те же колонки весов.
    expect(screen.getByText(/заехал гружёным, уехал пустым/)).toBeInTheDocument();
    expect(screen.getByText(/заехал пустым, уехал гружёным/)).toBeInTheDocument();
  });

  it("hides a group that has no rows", () => {
    renderTable([wagon({ id: 1, number: "Поезд-1", supplier: "ТОО Колос" })]);

    expect(screen.getByText("Приход")).toBeInTheDocument();
    expect(screen.queryByText("Вывоз")).not.toBeInTheDocument();
    expect(screen.queryByText(/заехал пустым, уехал гружёным/)).not.toBeInTheDocument();
  });

  it("shows only the selected direction and uses its empty-state route", () => {
    const wagons = [
      wagon({ id: 1, number: "Поезд-1" }),
      wagon({ id: 2, number: "123 ABC", direction: "passage", cargo_name: "Отруби" }),
    ];
    const { rerender } = render(<WagonTable wagons={wagons} me={me} emptyText="Нет вывоза" direction="passage" />);

    expect(screen.getByText("123 ABC")).toBeInTheDocument();
    expect(screen.queryByText("Поезд-1")).not.toBeInTheDocument();
    expect(screen.queryByText("Приход")).not.toBeInTheDocument();

    rerender(<WagonTable wagons={wagons.slice(0, 1)} me={me} emptyText="Нет вывоза" direction="passage" />);
    expect(screen.getByText("Нет вывоза")).toBeInTheDocument();
    expect(screen.getByText("Погрузка")).toBeInTheDocument();
    expect(screen.queryByText("Разгрузка")).not.toBeInTheDocument();
  });

  it("shows both weights and the net result for a finished passage", () => {
    renderTable([
      wagon({
        id: 2,
        number: "123 ABC",
        direction: "passage",
        cargo_name: "Отруби",
        status: "completed",
        status_label: "Завершён",
        entry_weight_kg: 12_000,
        exit_weight_kg: 30_000,
        net_weight_kg: 18_000,
      }),
    ]);

    const row = screen.getByRole("row", { name: /123 ABC/ });
    expect(within(row).getByText(/12\s*000/)).toBeInTheDocument();
    expect(within(row).getByText(/30\s*000/)).toBeInTheDocument();
    expect(within(row).getByText(/18\s*000/)).toBeInTheDocument();
  });

  it("labels a missing weight instead of leaving the cell blank", () => {
    renderTable([wagon({ id: 1, number: "Поезд-1" })]);

    const row = screen.getByRole("row", { name: /Поезд-1/ });
    expect(within(row).getAllByText("весы не подключены")).toHaveLength(2);
    expect(within(row).getByText("после разгрузки")).toBeInTheDocument();
  });

  it("offers the next weighing action per direction", () => {
    renderTable([
      wagon({ id: 1, number: "Поезд-1" }),
      wagon({ id: 2, number: "123 ABC", direction: "passage", cargo_name: "Отруби" }),
    ]);

    expect(screen.getByRole("link", { name: /Весы вагонов не подключены/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Взвесить пустую/ })).toBeInTheDocument();
  });

  it("shows the camera that supplied a recognized vehicle number", () => {
    renderTable([
      wagon({
        id: 2,
        number: "123ABC02",
        direction: "passage",
        cargo_name: "Отруби",
        number_source: "camera",
        number_camera_source: "cam1",
      }),
    ]);

    const row = screen.getByRole("row", { name: /123ABC02/ });
    expect(within(row).getByText("Камера cam1")).toBeInTheDocument();
    expect(within(row).getByText("Отруби")).toBeInTheDocument();
  });

  it("falls back to the empty state when there are no trips", () => {
    renderTable([]);

    expect(screen.getByText("Пусто")).toBeInTheDocument();
  });
});

describe("WagonTable — разбивка по дням", () => {
  /** Текст строк таблицы сверху вниз: заголовки направлений, дней и рейсы. */
  function rowTexts() {
    return screen.getAllByRole("row").map((row) => row.textContent ?? "");
  }

  function indexOf(prefix: string) {
    const index = rowTexts().findIndex((text) => text.startsWith(prefix));
    expect(index, prefix).toBeGreaterThan(-1);
    return index;
  }

  it("groups on-site rows by arrival day with «Сегодня»/«Вчера» headers and counts", () => {
    // Строки приходят вперемешку: день не должен повториться, а свежие — сверху.
    renderTable([
      wagon({ id: 1, number: "Поезд-1", arrived_at: "2026-09-05T18:00:00" }),
      wagon({ id: 2, number: "Поезд-2", arrived_at: "2026-09-06T09:00:00" }),
      wagon({ id: 3, number: "Поезд-3", arrived_at: "2026-09-01T08:00:00" }),
      wagon({ id: 4, number: "Поезд-4", arrived_at: "2026-09-06T11:30:00" }),
    ]);

    const today = screen.getByText("Сегодня").closest("tr") as HTMLTableRowElement;
    expect(within(today).getByText("2")).toBeInTheDocument();
    expect(screen.getAllByText("Сегодня")).toHaveLength(1);
    expect(screen.getByText("Вчера")).toBeInTheDocument();
    // Intl для ru-RU дописывает « г.»: «1 сентября 2026 г.».
    expect(screen.getByText(/^1 сентября 2026/)).toBeInTheDocument();

    expect(indexOf("Приход")).toBeLessThan(indexOf("Сегодня"));
    expect(indexOf("Сегодня")).toBeLessThan(indexOf("Поезд-4"));
    expect(indexOf("Поезд-4")).toBeLessThan(indexOf("Поезд-2"));
    expect(indexOf("Поезд-2")).toBeLessThan(indexOf("Вчера"));
    expect(indexOf("Вчера")).toBeLessThan(indexOf("Поезд-1"));
    expect(indexOf("Поезд-1")).toBeLessThan(indexOf("1 сентября 2026"));
    expect(indexOf("1 сентября 2026")).toBeLessThan(indexOf("Поезд-3"));
  });

  it("uses the exit day for finished rows and the arrival day otherwise", () => {
    renderTable([
      wagon({
        id: 5,
        number: "555 AAA",
        direction: "passage",
        status: "completed",
        status_label: "Завершён",
        arrived_at: "2026-09-05T08:00:00",
        exited_at: "2026-09-06T10:00:00",
      }),
      wagon({
        id: 6,
        number: "666 BBB",
        direction: "passage",
        arrived_at: "2026-09-05T09:00:00",
        exited_at: null,
      }),
    ]);

    expect(indexOf("Сегодня")).toBeLessThan(indexOf("555 AAA"));
    expect(indexOf("555 AAA")).toBeLessThan(indexOf("Вчера"));
    expect(indexOf("Вчера")).toBeLessThan(indexOf("666 BBB"));
  });

  it("keeps rows without any date in a last «Без даты» group", () => {
    renderTable([
      wagon({ id: 7, number: "Поезд-7" }),
      wagon({ id: 8, number: "Поезд-8", arrived_at: "2026-09-06T09:00:00" }),
      wagon({ id: 9, number: "Поезд-9", created_at: "2026-09-04T09:00:00" }),
    ]);

    expect(indexOf("Сегодня")).toBeLessThan(indexOf("Поезд-8"));
    expect(indexOf("Поезд-8")).toBeLessThan(indexOf("4 сентября 2026"));
    expect(indexOf("4 сентября 2026")).toBeLessThan(indexOf("Поезд-9"));
    expect(indexOf("Поезд-9")).toBeLessThan(indexOf("Без даты"));
    expect(indexOf("Без даты")).toBeLessThan(indexOf("Поезд-7"));
    const undated = screen.getByText("Без даты").closest("tr") as HTMLTableRowElement;
    expect(within(undated).getByText("1")).toBeInTheDocument();
  });

  it("splits days separately inside each direction group", () => {
    renderTable([
      wagon({ id: 1, number: "Поезд-1", arrived_at: "2026-09-06T09:00:00" }),
      wagon({
        id: 2,
        number: "123 ABC",
        direction: "passage",
        cargo_name: "Отруби",
        arrived_at: "2026-09-06T10:00:00",
      }),
    ]);

    expect(screen.getAllByText("Сегодня")).toHaveLength(2);
    expect(indexOf("Приход")).toBeLessThan(indexOf("Поезд-1"));
    expect(indexOf("Поезд-1")).toBeLessThan(indexOf("Вывоз"));
    expect(indexOf("Вывоз")).toBeLessThan(indexOf("123 ABC"));
  });
});

describe("WagonTable — удаление завершённого рейса", () => {
  beforeEach(() => {
    deleteMock.mockReset();
    deleteMock.mockResolvedValue({ data: { reverted_kg: 50_000 } });
  });

  function renderDeletable(wagons: GrainWagon[], user: Me = admin) {
    const onDeleted = vi.fn();
    render(<WagonTable wagons={wagons} me={user} emptyText="Пусто" onDeleted={onDeleted} />);
    return onDeleted;
  }

  it("hides delete without the grain.delete permission", () => {
    renderDeletable([wagon(finishedIntake)], me);

    expect(screen.queryByRole("button", { name: /Удалить рейс/ })).not.toBeInTheDocument();
  });

  it("allows an authorised employee to delete an on-site trip with an explicit warning and reason", async () => {
    const user = userEvent.setup();
    const onDeleted = renderDeletable([
      wagon({ id: 1, number: "Поезд-1", status: "at_silo", status_label: "У силоса" }),
    ]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс Поезд-1" }));

    expect(screen.getByText(/сейчас числится на территории/)).toBeInTheDocument();
    expect(screen.getByText(/это активный рейс/i)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Удалить активный рейс" });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByLabelText("Причина удаления *"), "Ошибочно созданный рейс");
    await user.click(confirm);

    expect(deleteMock).toHaveBeenCalledWith("/grain/wagons/1/delete/", {
      data: { reason: "Ошибочно созданный рейс" },
    });
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  });

  it("warns that intake grain returns to the silo, then deletes", async () => {
    const user = userEvent.setup();
    const onDeleted = renderDeletable([wagon(finishedIntake)]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс Поезд-9" }));
    // Оператор должен видеть последствие для остатка до подтверждения.
    expect(screen.getByText(/вернутся из силоса/)).toBeInTheDocument();

    const confirm = screen.getByRole("button", { name: "Удалить рейс" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("Причина удаления *"), "Ошибка при взвешивании");
    await user.click(screen.getByRole("button", { name: "Удалить рейс" }));

    expect(deleteMock).toHaveBeenCalledWith("/grain/wagons/9/delete/", {
      data: { reason: "Ошибка при взвешивании" },
    });
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  });

  it("requires the unrecorded-grain confirmation for an intake after unloading", async () => {
    const user = userEvent.setup();
    const onDeleted = renderDeletable([
      wagon({
        id: 14,
        number: "Приход-14",
        direction: "intake",
        status: "unloading_completed",
        status_label: "Разгрузка завершена",
      }),
    ]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс Приход-14" }));
    await user.type(screen.getByLabelText("Причина удаления *"), "Ошибочная разгрузка");

    const confirmation = screen.getByRole("checkbox", {
      name: "Подтверждаю: зерно уже учтено отдельно либо фактической разгрузки не было",
    });
    const confirm = screen.getByRole("button", { name: "Удалить активный рейс" });
    expect(confirm).toBeDisabled();

    await user.click(confirmation);
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(deleteMock).toHaveBeenCalledWith("/grain/wagons/14/delete/", {
      data: {
        reason: "Ошибочная разгрузка",
        confirm_unrecorded_grain_handled: true,
      },
    });
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  });

  it("tells the operator that a passage leaves stock untouched", async () => {
    const user = userEvent.setup();
    renderDeletable([
      wagon({
        id: 5,
        number: "777 AAA",
        direction: "passage",
        cargo_name: "Отруби",
        status: "completed",
        status_label: "Завершён",
        net_weight_kg: 18_000,
      }),
    ]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс 777 AAA" }));

    expect(screen.getByText(/Остатки склада не изменятся/)).toBeInTheDocument();
  });

  it("keeps the row and shows the error when deletion fails", async () => {
    deleteMock.mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    const onDeleted = renderDeletable([wagon(finishedIntake)]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс Поезд-9" }));
    await user.type(screen.getByLabelText("Причина удаления *"), "Дубликат");
    await user.click(screen.getByRole("button", { name: "Удалить рейс" }));

    expect(await screen.findByText("Ошибка удаления")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
