import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LoaderDaySummary } from "@/lib/loader";
import { todayLocalIsoDate } from "@/lib/utils";
import { DaySummarySheet } from "./day-summary-sheet";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: mocks.get },
}));

/** День фур: 30 мешков первого сорта и 5 высшего. */
const SUMMARY: LoaderDaySummary = {
  day: "2026-10-05",
  bags: 35,
  total_kg: "1750.00",
  products: [
    { label: "Первый сорт DIKHAN BABA NAN 50кг", quantity: 30 },
    { label: "Высший сорт Алтын Тәжі 50кг", quantity: 5 },
  ],
};

describe("DaySummarySheet", () => {
  beforeEach(() => {
    mocks.get.mockReset().mockResolvedValue({ data: SUMMARY });
  });

  it("всего отгружено за день — мешки и кг, и мешки каждой муки", async () => {
    render(<DaySummarySheet transport="truck" onClose={vi.fn()} />);

    const shipped = await screen.findByRole("region", { name: "Отгружено" });
    expect(shipped).toHaveTextContent("Всего отгружено35мешков1 750 кг");
    const rows = within(shipped).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Первый сорт DIKHAN BABA NAN 50кг30 мешков");
    expect(rows[1]).toHaveTextContent("Высший сорт Алтын Тәжі 50кг5 мешков");
    expect(mocks.get).toHaveBeenCalledWith(
      `/loader/day-summary/?transport=truck&day=${todayLocalIsoDate()}`,
      expect.anything(),
    );
  });

  it("другой день — новый запрос; у вагонов вес в тоннах", async () => {
    render(<DaySummarySheet transport="train" onClose={vi.fn()} />);
    await screen.findByRole("region", { name: "Отгружено" });

    fireEvent.change(screen.getByLabelText("День"), { target: { value: "2026-10-04" } });

    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/loader/day-summary/?transport=train&day=2026-10-04", expect.anything()),
    );
    expect(screen.getByRole("region", { name: "Отгружено" })).toHaveTextContent("1,75 т");
  });
});
