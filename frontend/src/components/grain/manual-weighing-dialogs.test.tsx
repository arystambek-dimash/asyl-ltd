import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GrainUnassignedWeighing, GrainWagon } from "@/lib/types";
import { ManualPassageEntryDialog } from "./manual-passage-entry-dialog";
import { ExitWeightCorrectionDialog } from "./exit-weight-correction-dialog";

const post = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ permissions: ["grain.correct_weighing"], is_superuser: false }));
vi.mock("@/lib/api", () => ({
  api: { post },
  apiError: () => "Вес уже изменён. Обновите рейс.",
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: auth }) }));

const ENTRY_TIME = "2026-01-01T10:00";
const REASON = "Начальный вес проверен по журналу весов";
const wagon = (extra: Partial<GrainWagon> = {}): GrainWagon =>
  ({
    id: 71,
    number: "904WLY13",
    direction: "passage",
    status: "completed",
    entry_weight_kg: 4100,
    exit_weight_kg: 9100,
    ...extra,
  }) as GrainWagon;
const savedExit = {
  id: 56,
  vehicle_number: "904WLY13",
  weight_kg: 8640,
  stable_weight_at: "2026-01-02T09:00:00Z",
  photo_url: "/api/grain/photos/unassigned/56/",
} as GrainUnassignedWeighing;

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function fillEntry(extra: { time?: string; weight?: string } = {}) {
  fill("Номер машины", "904wly13");
  fill("Начальный вес пустой машины, кг", extra.weight ?? "4100");
  fill("Фактическое время заезда", extra.time ?? ENTRY_TIME);
  fill("Причина ручного ввода", REASON);
}
function fillCorrection(weight = "9200") {
  fill("Выездной вес, кг", weight);
  fill("Причина исправления веса", REASON);
}

beforeEach(() => {
  auth.permissions = ["grain.correct_weighing"];
  auth.is_superuser = false;
  post.mockReset();
  post.mockResolvedValue({ data: {} });
});

describe("manual weighing access", () => {
  it("does not offer manual overrides to staff with ordinary weighing permission", () => {
    auth.permissions = ["grain.view", "grain.arrive", "grain.weigh"];
    render(
      <>
        <ManualPassageEntryDialog onChanged={vi.fn()} />
        <ExitWeightCorrectionDialog wagon={wagon()} onChanged={vi.fn()} />
      </>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("allows an administrator without explicit role permissions", () => {
    auth.permissions = [];
    auth.is_superuser = true;
    render(
      <>
        <ManualPassageEntryDialog onChanged={vi.fn()} />
        <ExitWeightCorrectionDialog wagon={wagon()} onChanged={vi.fn()} />
      </>,
    );
    expect(screen.getByRole("button", { name: "Заезд вручную" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Изменить выездной вес" })).toBeEnabled();
  });
});

describe("manual initial entry", () => {
  it("requires the actual entry time and audit reason, and creates an entry without photo data", async () => {
    const changed = vi.fn();
    const busyChanged = vi.fn();
    render(<ManualPassageEntryDialog onChanged={changed} onBusyChange={busyChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Заезд вручную" }));
    expect(busyChanged).toHaveBeenLastCalledWith(true);
    fill("Номер машины", "904wly13");
    fill("Начальный вес пустой машины, кг", "4100");
    fill("Причина ручного ввода", REASON);
    expect(screen.getByRole("button", { name: "Создать заезд" })).toBeDisabled();
    fill("Фактическое время заезда", ENTRY_TIME);
    fill("Причина ручного ввода", "");
    expect(screen.getByRole("button", { name: "Создать заезд" })).toBeDisabled();
    fill("Причина ручного ввода", REASON);
    await userEvent.click(screen.getByRole("button", { name: "Создать заезд" }));
    expect(post).toHaveBeenCalledExactlyOnceWith("/grain/passages/manual-entry/", {
      number: "904WLY13",
      cargo_name: "Отруби",
      entry_weight_kg: 4100,
      arrived_at: new Date(ENTRY_TIME).toISOString(),
      reason: REASON,
    });
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(busyChanged).toHaveBeenLastCalledWith(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("recovers a saved exit by its source ID, without copying or replacing its exit weight or photo", async () => {
    const changed = vi.fn();
    render(<ManualPassageEntryDialog item={savedExit} onChanged={changed} />);
    await userEvent.click(screen.getByRole("button", { name: "Указать начальный вес" }));
    expect(screen.getByLabelText("Номер машины")).toHaveValue("904WLY13");
    fillEntry();
    await userEvent.click(screen.getByRole("button", { name: "Создать и завершить рейс" }));
    expect(post).toHaveBeenCalledExactlyOnceWith("/grain/passages/manual-entry/", {
      number: "904WLY13",
      cargo_name: "Отруби",
      entry_weight_kg: 4100,
      arrived_at: new Date(ENTRY_TIME).toISOString(),
      reason: REASON,
      unassigned_weighing: 56,
    });
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  });

  it.each(["0", "-1", "4100.5", "1e3", "9007199254740992"])(
    "does not submit invalid initial weight %s",
    async (weight) => {
      render(<ManualPassageEntryDialog onChanged={vi.fn()} />);
      await userEvent.click(screen.getByRole("button", { name: "Заезд вручную" }));
      fillEntry({ weight });
      await userEvent.click(screen.getByRole("button", { name: "Создать заезд" }));
      expect(screen.getByRole("button", { name: "Создать заезд" })).toBeDisabled();
      expect(post).not.toHaveBeenCalled();
    },
  );

  it("rejects a future entry and an entry at or after the saved exit", async () => {
    render(
      <ManualPassageEntryDialog
        item={{ ...savedExit, stable_weight_at: new Date(ENTRY_TIME).toISOString() }}
        onChanged={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Указать начальный вес" }));
    for (const time of [ENTRY_TIME, "2999-01-01T00:00", "2026-01-03T10:00"]) {
      fillEntry({ time });
      expect(screen.getByRole("button", { name: "Создать и завершить рейс" })).toBeDisabled();
    }
    expect(post).not.toHaveBeenCalled();
  });

  it.each(["8640", "9000"])("rejects initial weight %s that produces no positive exported net", async (weight) => {
    render(<ManualPassageEntryDialog item={savedExit} onChanged={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Указать начальный вес" }));
    fillEntry({ weight });
    expect(screen.getByRole("button", { name: "Создать и завершить рейс" })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps entered evidence and the reason after an API failure so the operator can retry", async () => {
    post.mockRejectedValueOnce(new Error("conflict"));
    const changed = vi.fn();
    const busyChanged = vi.fn();
    render(<ManualPassageEntryDialog onChanged={changed} onBusyChange={busyChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Заезд вручную" }));
    fillEntry();
    await userEvent.click(screen.getByRole("button", { name: "Создать заезд" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Вес уже изменён");
    expect(screen.getByRole("dialog", { name: "Заезд без фото" })).toBeInTheDocument();
    expect(screen.getByLabelText("Причина ручного ввода")).toHaveValue(REASON);
    expect(screen.getByLabelText("Фактическое время заезда")).toHaveValue(ENTRY_TIME);
    expect(screen.getByLabelText("Начальный вес пустой машины, кг")).toHaveValue("4100");
    expect(busyChanged).toHaveBeenLastCalledWith(true);
    expect(changed).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Создать заезд" }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledTimes(2);
  });
});

describe("exit weight correction", () => {
  it("requires an audit reason before changing the recorded exit weight", async () => {
    render(<ExitWeightCorrectionDialog wagon={wagon()} onChanged={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Изменить выездной вес" }));
    fill("Выездной вес, кг", "9200");
    expect(screen.getByRole("button", { name: "Сохранить исправление" })).toBeDisabled();
    fill("Причина исправления веса", "ок");
    expect(screen.getByRole("button", { name: "Сохранить исправление" })).toBeDisabled();
    fill("Причина исправления веса", REASON);
    expect(screen.getByRole("button", { name: "Сохранить исправление" })).toBeEnabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("submits the exit weight observed at dialog opening, even if background data changes", async () => {
    const changed = vi.fn();
    const { rerender } = render(<ExitWeightCorrectionDialog wagon={wagon()} onChanged={changed} />);
    await userEvent.click(screen.getByRole("button", { name: "Изменить выездной вес" }));
    fillCorrection();
    rerender(<ExitWeightCorrectionDialog wagon={wagon({ exit_weight_kg: 9300 })} onChanged={changed} />);
    expect(screen.getByLabelText("Выездной вес, кг")).toHaveValue("9200");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить исправление" }));
    expect(post).toHaveBeenCalledExactlyOnceWith("/grain/passages/71/correct-exit-weight/", {
      exit_weight_kg: 9200,
      expected_exit_weight_kg: 9100,
      reason: REASON,
    });
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  });

  it("sends an explicit null expectation when recording the first exit weight", async () => {
    const changed = vi.fn();
    render(
      <ExitWeightCorrectionDialog wagon={wagon({ status: "at_silo", exit_weight_kg: null })} onChanged={changed} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Внести выездной вес вручную" }));
    fillCorrection();
    await userEvent.click(screen.getByRole("button", { name: "Записать вес и завершить рейс" }));
    expect(post).toHaveBeenCalledExactlyOnceWith("/grain/passages/71/correct-exit-weight/", {
      exit_weight_kg: 9200,
      expected_exit_weight_kg: null,
      reason: REASON,
    });
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  });

  it.each(["4100", "4000", "0", "-1", "9100", "9200.5", "1e4"])(
    "does not submit invalid, unchanged, or net-invalid exit weight %s",
    async (weight) => {
      render(<ExitWeightCorrectionDialog wagon={wagon()} onChanged={vi.fn()} />);
      await userEvent.click(screen.getByRole("button", { name: "Изменить выездной вес" }));
      fillCorrection(weight);
      await userEvent.click(screen.getByRole("button", { name: "Сохранить исправление" }));
      expect(screen.getByRole("button", { name: "Сохранить исправление" })).toBeDisabled();
      expect(post).not.toHaveBeenCalled();
    },
  );

  it("preserves the correction and reason on a concurrent update error", async () => {
    post.mockRejectedValueOnce(new Error("conflict"));
    const changed = vi.fn();
    const busyChanged = vi.fn();
    render(<ExitWeightCorrectionDialog wagon={wagon()} onChanged={changed} onBusyChange={busyChanged} />);
    await userEvent.click(screen.getByRole("button", { name: "Изменить выездной вес" }));
    fillCorrection();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить исправление" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Вес уже изменён");
    expect(screen.getByRole("dialog", { name: "Выездной вес" })).toBeInTheDocument();
    expect(screen.getByLabelText("Выездной вес, кг")).toHaveValue("9200");
    expect(screen.getByLabelText("Причина исправления веса")).toHaveValue(REASON);
    expect(changed).not.toHaveBeenCalled();
    expect(busyChanged).toHaveBeenLastCalledWith(true);
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(busyChanged).toHaveBeenLastCalledWith(false);
  });

  it.each([
    { direction: "intake" as const },
    { status: "cancelled" },
    { status: "exited" },
    { status: "arrived" },
    { entry_weight_kg: null },
  ])("does not offer correction outside a weighed export trip: %o", (extra) => {
    render(<ExitWeightCorrectionDialog wagon={wagon(extra)} onChanged={vi.fn()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
