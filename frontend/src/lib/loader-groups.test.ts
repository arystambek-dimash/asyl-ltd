import { describe, expect, it } from "vitest";
import {
  groupByPlannedDay,
  inHistoryRange,
  initialLoaderTransport,
  inQueueFilter,
  loaderTransports,
  plannedDay,
  shortDate,
  withHistoryRow,
  withQueueRow,
} from "./loader-groups";
import type { LoaderOrder } from "./loader";
import type { Me } from "./types";

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

describe("loaderTransports", () => {
  const me = (permissions: string[], is_superuser = false) => ({ id: 1, is_superuser, permissions }) as Me;

  it("opens a tab per area the loader holds, trucks first", () => {
    expect(loaderTransports(me(["loader.view", "loader.trucks", "loader.wagons"]))).toEqual(["truck", "train"]);
    expect(loaderTransports(me(["loader.view", "loader.wagons"]))).toEqual(["train"]);
    expect(loaderTransports(me(["loader.view"]))).toEqual([]);
    expect(loaderTransports(me([], true))).toEqual(["truck", "train"]);
    expect(loaderTransports(null)).toEqual([]);
  });
});

describe("initialLoaderTransport", () => {
  it("reopens the last tab while it is still allowed", () => {
    expect(initialLoaderTransport(["truck", "train"], "train")).toBe("train");
    expect(initialLoaderTransport(["truck"], "train")).toBe("truck");
    expect(initialLoaderTransport(["truck", "train"], "ship")).toBe("truck");
    expect(initialLoaderTransport(["train"], null)).toBe("train");
    expect(initialLoaderTransport([], "truck")).toBeNull();
  });
});

describe("withQueueRow", () => {
  it("puts a returned order back in the server order: planned day, then number", () => {
    const rows = [order(3, { arrival_date: "2026-09-17" }), order(9, { arrival_date: "2026-09-18" })];

    expect(withQueueRow(rows, order(5, { arrival_date: "2026-09-18" })).map((row) => row.id)).toEqual([3, 5, 9]);
    expect(withQueueRow(rows, order(1, { arrival_date: "2026-09-20" })).map((row) => row.id)).toEqual([3, 9, 1]);
  });

  it("replaces a row that is already shown", () => {
    const rows = [order(3, { truck_number: "" }), order(4)];

    const next = withQueueRow(rows, order(3, { truck_number: "403BJN13" }));

    expect(next.map((row) => [row.id, row.truck_number])).toEqual([
      [3, "403BJN13"],
      [4, ""],
    ]);
  });
});

describe("inQueueFilter", () => {
  const today = "2026-09-18";
  const planned = (day: string) => order(1, { arrival_date: day });
  const filter = (fields: { day?: string; overdue?: string; search?: string }) => ({
    day: "",
    overdue: "",
    search: "",
    ...fields,
  });

  it("matches the day the queue shows, the way the server filters", () => {
    expect(inQueueFilter(planned(today), filter({ day: today }), today)).toBe(true);
    expect(inQueueFilter(planned("2026-09-17"), filter({ day: today }), today)).toBe(false);
    expect(inQueueFilter(planned("2026-09-19"), filter({ day: "2026-09-19" }), today)).toBe(true);
  });

  it("keeps only earlier days under «Просрочено» and every day under «Все»", () => {
    expect(inQueueFilter(planned("2026-09-17"), filter({ overdue: "1" }), today)).toBe(true);
    expect(inQueueFilter(planned(today), filter({ overdue: "1" }), today)).toBe(false);
    expect(inQueueFilter(planned("2026-01-01"), filter({}), today)).toBe(true);
  });

  it("leaves a search to the server", () => {
    expect(inQueueFilter(planned(today), filter({ day: today, search: "Мурат" }), today)).toBe(false);
  });
});

describe("inHistoryRange", () => {
  const today = "2026-09-23";
  const shipped = (at: string) => order(1, { status: "shipped", shipped_at: at });
  const range = { from: "2026-09-17", to: today };

  it("matches the shipped day against the shown range", () => {
    expect(inHistoryRange(shipped("2026-09-19T12:00:00+05:00"), range, "", today)).toBe(true);
    expect(inHistoryRange(shipped("2026-09-16T12:00:00+05:00"), range, "", today)).toBe(false);
    expect(inHistoryRange(shipped("2026-09-19T12:00:00+05:00"), { from: today, to: today }, "", today)).toBe(false);
  });

  it("leaves a search to the server", () => {
    expect(inHistoryRange(shipped("2026-09-19T12:00:00+05:00"), range, "2808", today)).toBe(false);
  });
});

describe("withHistoryRow", () => {
  it("puts a backdated report shipment on its own day, latest first", () => {
    const rows = [
      order(9, { shipped_at: "2026-09-23T10:00:00+05:00" }),
      order(4, { shipped_at: "2026-09-18T10:00:00+05:00" }),
    ];

    const next = withHistoryRow(rows, order(12, { shipped_at: "2026-09-19T12:00:00+05:00" }));

    expect(next.map((row) => row.id)).toEqual([9, 12, 4]);
    expect(withHistoryRow(rows, order(13, { shipped_at: "2026-09-23T11:00:00+05:00" }))[0].id).toBe(13);
    expect(withHistoryRow(rows, order(9, { shipped_at: "2026-09-23T10:00:00+05:00" })).map((row) => row.id)).toEqual([
      9, 4,
    ]);
  });
});
