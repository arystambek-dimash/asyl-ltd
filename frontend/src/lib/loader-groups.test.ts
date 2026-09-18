import { describe, expect, it } from "vitest";
import { groupByPlannedDay, plannedDay, shortDate } from "./loader-groups";
import type { LoaderOrder } from "./loader";

const order = (id: number, fields: Partial<LoaderOrder> = {}): LoaderOrder =>
  ({
    id,
    status: "confirmed",
    transport_type: "truck",
    truck_number: "",
    currency: "KZT",
    arrival_date: null,
    created_at: "2026-09-16T10:00:00+05:00",
    client_name: "Клиент",
    items: [],
    bags: 1,
    total_kg: "50.00",
    total_amount: "1000.00",
    shipped_at: null,
    ...fields,
  }) as LoaderOrder;

describe("plannedDay", () => {
  it("prefers the planned arrival date and falls back to the order day", () => {
    expect(plannedDay(order(1, { arrival_date: "2026-09-20" }))).toBe("2026-09-20");
    expect(plannedDay(order(2))).toBe("2026-09-16");
  });
});

describe("groupByPlannedDay", () => {
  const today = "2026-09-18";

  it("puts overdue first, then today and tomorrow", () => {
    const groups = groupByPlannedDay(
      [
        order(1, { arrival_date: "2026-09-19" }),
        order(2, { arrival_date: "2026-09-18" }),
        order(3, { arrival_date: "2026-09-16" }),
        order(4, { arrival_date: "2026-09-17" }),
      ],
      today,
    );

    expect(groups.map((group) => [group.day, group.label, group.overdue])).toEqual([
      ["2026-09-16", "ПРОСРОЧЕНО", true],
      ["2026-09-17", "ПРОСРОЧЕНО", true],
      ["2026-09-18", "СЕГОДНЯ", false],
      ["2026-09-19", "ЗАВТРА", false],
    ]);
  });

  it("labels later days with the date alone and keeps their orders together", () => {
    const groups = groupByPlannedDay(
      [order(5, { arrival_date: "2026-09-25" }), order(6, { arrival_date: "2026-09-25" })],
      today,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("");
    expect(groups[0].date).toBe("25.09");
    expect(groups[0].orders.map((row) => row.id)).toEqual([5, 6]);
  });
});

describe("shortDate", () => {
  it("shows the day and month the way the badge does", () => {
    expect(shortDate("2026-08-11")).toBe("11.08");
  });
});
