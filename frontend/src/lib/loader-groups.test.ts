import { describe, expect, it } from "vitest";
import {
  groupByPlannedDay,
  inHistoryRange,
  initialLoaderTransport,
  inQueueFilter,
  loaderTransports,
  withHistoryRow,
  withQueueRow,
} from "./loader-groups";
import { makeLoaderOrder, makeMe } from "@/test-utils/factories";

describe("groupByPlannedDay", () => {
  const today = "2026-09-18";

  it("puts overdue first, then today and tomorrow", () => {
    const groups = groupByPlannedDay(
      [
        makeLoaderOrder(1, { planned_on: "2026-09-19" }),
        makeLoaderOrder(2, { planned_on: "2026-09-18" }),
        makeLoaderOrder(3, { planned_on: "2026-09-16" }),
        makeLoaderOrder(4, { planned_on: "2026-09-17" }),
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
      [makeLoaderOrder(5, { planned_on: "2026-09-25" }), makeLoaderOrder(6, { planned_on: "2026-09-25" })],
      today,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("");
    expect(groups[0].date).toBe("25.09");
    expect(groups[0].orders.map((row) => row.id)).toEqual([5, 6]);
  });
});

describe("loaderTransports", () => {
  const me = (permissions: string[], is_superuser = false) => makeMe({ is_superuser, permissions });

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
    const rows = [makeLoaderOrder(3, { planned_on: "2026-09-17" }), makeLoaderOrder(9, { planned_on: "2026-09-18" })];

    expect(withQueueRow(rows, makeLoaderOrder(5, { planned_on: "2026-09-18" })).map((row) => row.id)).toEqual([
      3, 5, 9,
    ]);
    expect(withQueueRow(rows, makeLoaderOrder(1, { planned_on: "2026-09-20" })).map((row) => row.id)).toEqual([
      3, 9, 1,
    ]);
  });

  it("replaces a row that is already shown", () => {
    const rows = [makeLoaderOrder(3, { truck_number: "" }), makeLoaderOrder(4)];

    const next = withQueueRow(rows, makeLoaderOrder(3, { truck_number: "403BJN13" }));

    expect(next.map((row) => [row.id, row.truck_number])).toEqual([
      [3, "403BJN13"],
      [4, ""],
    ]);
  });
});

describe("inQueueFilter", () => {
  const today = "2026-09-18";
  const planned = (day: string) => makeLoaderOrder(1, { planned_on: day });
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
  const shipped = (at: string) => makeLoaderOrder(1, { status: "shipped", shipped_at: at });
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
      makeLoaderOrder(9, { shipped_at: "2026-09-23T10:00:00+05:00" }),
      makeLoaderOrder(4, { shipped_at: "2026-09-18T10:00:00+05:00" }),
    ];

    const next = withHistoryRow(rows, makeLoaderOrder(12, { shipped_at: "2026-09-19T12:00:00+05:00" }));

    expect(next.map((row) => row.id)).toEqual([9, 12, 4]);
    expect(withHistoryRow(rows, makeLoaderOrder(13, { shipped_at: "2026-09-23T11:00:00+05:00" }))[0].id).toBe(13);
    expect(
      withHistoryRow(rows, makeLoaderOrder(9, { shipped_at: "2026-09-23T10:00:00+05:00" })).map((row) => row.id),
    ).toEqual([9, 4]);
  });
});
