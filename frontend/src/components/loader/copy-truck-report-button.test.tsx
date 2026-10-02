import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CopyTruckReportButton } from "./copy-truck-report-button";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get },
  apiError: (error: Error) => error.message,
}));

const TEXT = "Отгрузка 02.10.26\nkz 909 ERD 13\nНуржан Сарыагаш 87029368080\nД1с(50кг)- 22,5 тн\nСклад: Мельница";

/** Запись в буфер, какой её видит браузер: ClipboardItem хранит обещание содержимого. */
class FakeClipboardItem {
  constructor(readonly items: Record<string, Promise<Blob>>) {}
}

describe("CopyTruckReportButton", () => {
  const writeText = vi.fn();

  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue({ data: { text: TEXT, order_ids: [624] } });
    writeText.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("копирует текст сервера и на две секунды говорит «Скопировано ✓»", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<CopyTruckReportButton scope={{ order: 624 }} />);

    fireEvent.click(screen.getByRole("button", { name: "Скопировать отчёт" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Скопировано ✓" })).toBeEnabled());
    expect(mocks.get).toHaveBeenCalledWith("/loader/truck-report/?order=624");
    expect(writeText).toHaveBeenCalledWith(TEXT);
    // Эффект ответа ставит таймер надписи — дадим ему отработать до перемотки времени.
    await act(async () => undefined);
    await act(async () => {
      vi.advanceTimersByTime(1900);
    });
    expect(screen.getByRole("button", { name: "Скопировано ✓" })).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(100);
    });
    expect(screen.getByRole("button", { name: "Скопировать отчёт" })).toBeInTheDocument();
  });

  it("начинает запись в буфер в самом нажатии, до ответа сервера (Safari на iPhone)", async () => {
    const write = vi.fn(async ([item]: FakeClipboardItem[]) => {
      await item.items["text/plain"];
    });
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("navigator", { clipboard: { write, writeText } });
    let answer!: (value: { data: { text: string } }) => void;
    mocks.get.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    render(<CopyTruckReportButton scope={{ date_from: "2026-10-02", date_to: "2026-10-02", search: "" }} />);

    fireEvent.click(screen.getByRole("button", { name: "Скопировать отчёт" }));

    expect(write).toHaveBeenCalledTimes(1);
    answer({ data: { text: TEXT } });
    expect(await screen.findByRole("button", { name: "Скопировано ✓" })).toBeInTheDocument();
    const blob = await write.mock.calls[0][0][0].items["text/plain"];
    expect(await blob.text()).toBe(TEXT);
    expect(writeText).not.toHaveBeenCalled();
  });

  it("ошибка сервера — под кнопкой, «Скопировано» не появляется", async () => {
    mocks.get.mockRejectedValue(new Error("Запись не найдена — возможно, её уже удалили."));
    render(<CopyTruckReportButton scope={{ order: 624 }} />);

    fireEvent.click(screen.getByRole("button", { name: "Скопировать отчёт" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Запись не найдена — возможно, её уже удалили.");
    expect(screen.getByRole("button", { name: "Скопировать отчёт" })).toBeEnabled();
    expect(writeText).not.toHaveBeenCalled();
  });

  it("браузер не дал скопировать — так и говорит", async () => {
    vi.stubGlobal("navigator", {});
    document.execCommand = vi.fn().mockReturnValue(false);
    render(<CopyTruckReportButton scope={{ order: 624 }} label="Скопировать все" />);

    fireEvent.click(screen.getByRole("button", { name: "Скопировать все" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось скопировать");
    expect(screen.queryByRole("button", { name: "Скопировано ✓" })).not.toBeInTheDocument();
  });
});
