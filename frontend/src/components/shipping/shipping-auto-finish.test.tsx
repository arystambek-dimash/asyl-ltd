import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ShippingAutoFinish } from "@/lib/types";
import { ShippingAutoFinishDetails } from "./shipping-auto-finish";

const waiting: ShippingAutoFinish = {
  state: "waiting",
  remaining_seconds: 40,
  observed_at: "2026-09-08T05:00:00Z",
  detail: "Транспорт отсутствует, конвейер пуст. Ожидаем 40 секунд.",
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-08T06:00:00Z"));
});
afterEach(() => vi.useRealTimers());

describe("ShippingAutoFinishDetails", () => {
  it("counts from receipt independently of PC clock and waits for server completion at zero", () => {
    const { rerender } = render(<ShippingAutoFinishDetails value={{ ...waiting, remaining_seconds: 3 }} />);
    expect(screen.getByText("Автозавершение через 3 с")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2_000));
    expect(screen.getByText("Автозавершение через 1 с")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByText("Проверяем завершение погрузки")).toBeInTheDocument();
    expect(screen.queryByText("Погрузка завершена автоматически")).not.toBeInTheDocument();
    rerender(<ShippingAutoFinishDetails value={{ ...waiting, state: "completed", remaining_seconds: null }} />);
    expect(screen.getByText("Погрузка завершена автоматически")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("drops a stale countdown and requires a fresh observation before resuming", () => {
    const { rerender } = render(<ShippingAutoFinishDetails value={waiting} />);
    act(() => vi.advanceTimersByTime(15_000));
    expect(screen.getByText("Автозавершение: нет свежих данных")).toBeInTheDocument();
    expect(screen.queryByText(/Автозавершение через/)).not.toBeInTheDocument();
    rerender(
      <ShippingAutoFinishDetails value={{ ...waiting, observed_at: "2026-09-08T05:00:15Z", remaining_seconds: 25 }} />,
    );
    expect(screen.getByText("Автозавершение через 25 с")).toBeInTheDocument();
    rerender(<ShippingAutoFinishDetails value={waiting} stale />);
    expect(screen.getByText("Автозавершение: нет свежих данных")).toBeInTheDocument();
  });

  it("shows why a timer was reset and does not keep decrementing while blocked", () => {
    const { rerender } = render(<ShippingAutoFinishDetails value={waiting} />);
    rerender(
      <ShippingAutoFinishDetails
        value={{
          ...waiting,
          state: "blocked",
          remaining_seconds: null,
          detail: "На конвейере есть мешки. Отсчёт сброшен.",
        }}
      />,
    );
    act(() => vi.advanceTimersByTime(40_000));
    expect(screen.getByText("Автозавершение приостановлено")).toBeInTheDocument();
    expect(screen.getByText("На конвейере есть мешки. Отсчёт сброшен.")).toBeInTheDocument();
    expect(screen.queryByText(/Автозавершение через/)).not.toBeInTheDocument();
  });

  it("preserves a saved historical wait without running its countdown", () => {
    render(<ShippingAutoFinishDetails value={{ ...waiting, remaining_seconds: 12 }} historical stale />);
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByText("До автозавершения оставалось 12 с")).toBeInTheDocument();
    expect(screen.getByText(/Зафиксировано:/)).toBeInTheDocument();
    expect(screen.queryByText("Погрузка завершена автоматически")).not.toBeInTheDocument();
  });
});
