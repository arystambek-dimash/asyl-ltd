import { afterEach, describe, expect, it, vi } from "vitest";
import {
  answersComplete,
  dispatchSourcesPayload,
  hasTruckReport,
  historyReportUrl,
  loadWeight,
  readStoredLoaderTransport,
  readStoredOverdueDays,
  sameSourceContext,
  shortageNote,
  sourcesText,
  splitLabel,
  splitRemainder,
  storeLoaderTransport,
  storeOverdueDays,
  truckReportText,
  warehouseTone,
  type DispatchSources,
} from "./loader";

const apiGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: { get: apiGet },
}));

describe("loadWeight", () => {
  it("shows a wagon in tonnes and a truck in kilograms", () => {
    expect(loadWeight({ transport_type: "train", total_kg: "68000.00" })).toBe("68 т");
    expect(loadWeight({ transport_type: "train", total_kg: "1360.00" })).toBe("1,36 т");
    // Разряды у фуры — как у всех весов в CRM: «3 500 кг» (Intl ставит U+00A0).
    expect(loadWeight({ transport_type: "truck", total_kg: "3500.00" })).toBe("3\u00a0500 кг");
    expect(loadWeight({ transport_type: "train", total_kg: "67555.00" })).toBe("67,56 т");
  });
});

describe("history reports", () => {
  it("one shipment or the history filters; an empty search is not sent", () => {
    expect(historyReportUrl("wagon-report/compose", { order: 366 })).toBe("/loader/wagon-report/compose/?order=366");
    expect(
      historyReportUrl("wagon-report/compose", { date_from: "2026-09-18", date_to: "2026-09-24", search: "OSIYO" }),
    ).toBe("/loader/wagon-report/compose/?date_from=2026-09-18&date_to=2026-09-24&search=OSIYO");
    expect(historyReportUrl("truck-report", { date_from: "2026-10-02", date_to: "2026-10-02", search: "" })).toBe(
      "/loader/truck-report/?date_from=2026-10-02&date_to=2026-10-02",
    );
  });

  it("only a shipped truck has a report to copy; wagons are reported by the bot", () => {
    expect(hasTruckReport({ transport_type: "truck", status: "shipped" })).toBe(true);
    expect(hasTruckReport({ transport_type: "truck", status: "confirmed" })).toBe(false);
    expect(hasTruckReport({ transport_type: "train", status: "shipped" })).toBe(false);
  });

  it("the truck report text is composed by the server", async () => {
    apiGet.mockResolvedValue({ data: { text: "Отгрузка 02.10.26\nkz 909 ERD 13", order_ids: [624] } });

    await expect(truckReportText({ order: 624 })).resolves.toBe("Отгрузка 02.10.26\nkz 909 ERD 13");
    expect(apiGet).toHaveBeenCalledWith("/loader/truck-report/?order=624");
  });
});

describe("loader tab storage", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("remembers the last tab per user", () => {
    storeLoaderTransport("train", 7);

    expect(readStoredLoaderTransport(7)).toBe("train");
    expect(readStoredLoaderTransport(8)).toBeNull();
  });

  it("remembers the overdue window per user and per tab, 3 days by default", () => {
    expect(readStoredOverdueDays("truck", 7)).toBe(3);
    storeOverdueDays(7, "truck", 7);

    expect(readStoredOverdueDays("truck", 7)).toBe(7);
    expect(readStoredOverdueDays("train", 7)).toBe(3);
    expect(readStoredOverdueDays("truck", 8)).toBe(3);
    // Не из выбора (старое или чужое значение) — окно по умолчанию.
    localStorage.setItem("loader:overdue-days:truck:7", "5");
    expect(readStoredOverdueDays("truck", 7)).toBe(3);
  });

  it("survives a browser without storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(() => storeLoaderTransport("truck", 7)).not.toThrow();
    expect(readStoredLoaderTransport(7)).toBeNull();
    expect(() => storeOverdueDays(7, "truck", 7)).not.toThrow();
    expect(readStoredOverdueDays("truck", 7)).toBe(3);
  });
});

/** Пример из дизайна: Д1с 20 мешков (на «Мельнице 2» карточки нет), Б 40 мешков. */
const context: DispatchSources = {
  choose: true,
  warehouses: [
    { id: 1, name: "Мельница" },
    { id: 2, name: "Мельница 2" },
  ],
  products: [
    { product: 12, label: "Д1с · 50 кг", bags: 20, short: { "2": 0 } },
    { product: 15, label: "Б · 25 кг", bags: 40, short: {} },
  ],
};
const [flour, bran] = context.products;

describe("dispatchSourcesPayload", () => {
  it("flattens answers by product then warehouse and drops zero parts", () => {
    expect(dispatchSourcesPayload({ 15: { 2: 40 }, 12: { 2: 8, 1: 12, 3: 0 } })).toEqual([
      { product: 12, warehouse: 1, bags: 12 },
      { product: 12, warehouse: 2, bags: 8 },
      { product: 15, warehouse: 2, bags: 40 },
    ]);
  });

  it("sends nothing without answers", () => {
    expect(dispatchSourcesPayload({})).toEqual([]);
    expect(dispatchSourcesPayload({ 12: { 1: 0 } })).toEqual([]);
  });
});

describe("shortageNote", () => {
  it("warns only at the warehouse that runs short", () => {
    expect(shortageNote(flour, 2, 20)).toBe("осталось 0 — уйдёт в минус");
    expect(shortageNote(flour, 1, 20)).toBe("");
    expect(shortageNote(bran, 2, 40)).toBe("");
  });

  it("compares the part taken, not the whole order", () => {
    const product = { ...flour, short: { "2": 10 } };

    expect(shortageNote(product, 2, 8)).toBe("");
    expect(shortageNote(product, 2, 10)).toBe("");
    expect(shortageNote(product, 2, 11)).toBe("осталось 10 — уйдёт в минус");
  });

  it("shows a negative balance as zero and stays quiet for an empty part", () => {
    const product = { ...flour, short: { "2": -7 } };

    expect(shortageNote(product, 2, 5)).toBe("осталось 0 — уйдёт в минус");
    expect(shortageNote(product, 2, 0)).toBe("");
  });
});

describe("sourcesText", () => {
  it("names the only warehouse like the waybill", () => {
    expect(sourcesText(context, { 2: 20 })).toBe("со склада: Мельница 2");
    expect(sourcesText(context, { 1: 20, 2: 0 })).toBe("со склада: Мельница");
  });

  it("lists a split in warehouse order", () => {
    expect(sourcesText(context, { 2: 8, 1: 12 })).toBe("Мельница — 12, Мельница 2 — 8");
  });

  it("is empty before an answer", () => {
    expect(sourcesText(context, {})).toBe("");
  });
});

describe("splitLabel", () => {
  it("says two warehouses or several", () => {
    expect(splitLabel(2)).toBe("С двух складов…");
    expect(splitLabel(3)).toBe("С нескольких складов…");
    expect(splitLabel(5)).toBe("С нескольких складов…");
  });
});

describe("splitRemainder", () => {
  it("leaves the rest of the bags for the last warehouse", () => {
    expect(splitRemainder(20, [12])).toBe(8);
    expect(splitRemainder(20, [])).toBe(20);
    expect(splitRemainder(20, [12, 8])).toBe(0);
    expect(splitRemainder(20, [12, 10])).toBe(-2);
  });
});

describe("sameSourceContext", () => {
  it("keeps answers when only balances or names changed", () => {
    const later: DispatchSources = {
      ...context,
      warehouses: [
        { id: 1, name: "Мельница (основная)" },
        { id: 2, name: "Мельница 2" },
      ],
      products: context.products.map((product) => ({ ...product, label: `${product.label}!`, short: {} })),
    };

    expect(sameSourceContext(context, later)).toBe(true);
  });

  it("drops answers when the order or the warehouses changed", () => {
    const moreBags = { ...context, products: [{ ...flour, bags: 21 }, bran] };
    const otherProduct = { ...context, products: [flour, { ...bran, product: 16 }] };
    const lessProducts = { ...context, products: [flour] };
    const thirdWarehouse = { ...context, warehouses: [...context.warehouses, { id: 3, name: "Мельница 3" }] };
    const otherWarehouse = { ...context, warehouses: [context.warehouses[0], { id: 4, name: "Мельница 2" }] };

    for (const changed of [moreBags, otherProduct, lessProducts, thirdWarehouse, otherWarehouse]) {
      expect(sameSourceContext(context, changed)).toBe(false);
    }
  });
});

describe("answersComplete", () => {
  it("is complete when every product is fully placed", () => {
    expect(answersComplete(context, { 12: { 1: 12, 2: 8 }, 15: { 2: 40 } })).toBe(true);
  });

  it("is incomplete while a product is unanswered or its split does not add up", () => {
    expect(answersComplete(context, {})).toBe(false);
    expect(answersComplete(context, { 12: { 1: 20 } })).toBe(false);
    expect(answersComplete(context, { 12: { 1: 12, 2: 5 }, 15: { 2: 40 } })).toBe(false);
    expect(answersComplete(context, { 12: { 1: 21 }, 15: { 2: 40 } })).toBe(false);
  });
});

describe("warehouseTone", () => {
  it("gives each warehouse its own colour, fixed by id", () => {
    expect(warehouseTone(1)).not.toBe(warehouseTone(2));
    expect(warehouseTone(2)).toBe(warehouseTone(2));
  });

  it("never uses the bag colours (red, green, blue)", () => {
    const bagColours = ["#dc2626", "#16a34a", "#2563eb"];
    for (const id of [1, 2, 3, 4, 5]) expect(bagColours).not.toContain(warehouseTone(id).toLowerCase());
  });
});
