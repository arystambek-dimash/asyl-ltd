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
    expect(within(row).getAllByText("ждёт весов")).toHaveLength(2);
    expect(within(row).getByText("после разгрузки")).toBeInTheDocument();
  });

  it("offers the next weighing action per direction", () => {
    renderTable([
      wagon({ id: 1, number: "Поезд-1" }),
      wagon({ id: 2, number: "123 ABC", direction: "passage", cargo_name: "Отруби" }),
    ]);

    expect(screen.getByRole("link", { name: /Внести вес на въезде/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Взвесить пустую/ })).toBeInTheDocument();
  });

  it("falls back to the empty state when there are no trips", () => {
    renderTable([]);

    expect(screen.getByText("Пусто")).toBeInTheDocument();
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

  it("hides delete while the trip is still on site", () => {
    renderDeletable([wagon({ id: 1, number: "Поезд-1", status: "at_silo", status_label: "У силоса" })]);

    expect(screen.queryByRole("button", { name: /Удалить рейс/ })).not.toBeInTheDocument();
  });

  it("warns that intake grain returns to the silo, then deletes", async () => {
    const user = userEvent.setup();
    const onDeleted = renderDeletable([wagon(finishedIntake)]);

    await user.click(screen.getByRole("button", { name: "Удалить рейс Поезд-9" }));
    // Оператор должен видеть последствие для остатка до подтверждения.
    expect(screen.getByText(/вернутся из силоса/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Удалить рейс" }));

    expect(deleteMock).toHaveBeenCalledWith("/grain/wagons/9/delete/");
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
    await user.click(screen.getByRole("button", { name: "Удалить рейс" }));

    expect(await screen.findByText("Ошибка удаления")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
