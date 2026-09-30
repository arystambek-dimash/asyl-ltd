import { afterEach, describe, expect, it, vi } from "vitest";
import {
  answersComplete,
  dispatchSourcesPayload,
  loadWeight,
  readStoredLoaderTransport,
  sameSourceContext,
  shortageNote,
  sourcesText,
  splitLabel,
  splitRemainder,
  storeLoaderTransport,
  type DispatchSources,
} from "./loader";

describe("loadWeight", () => {
  it("shows a wagon in tonnes and a truck in kilograms", () => {
    expect(loadWeight({ transport_type: "train", total_kg: "68000.00" })).toBe("68 т");
    expect(loadWeight({ transport_type: "train", total_kg: "1360.00" })).toBe("1,36 т");
    // Разряды у фуры — как у всех весов в CRM: «3 500 кг» (Intl ставит U+00A0).
    expect(loadWeight({ transport_type: "truck", total_kg: "3500.00" })).toBe("3\u00a0500 кг");
    expect(loadWeight({ transport_type: "train", total_kg: "67555.00" })).toBe("67,56 т");
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

  it("survives a browser without storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(() => storeLoaderTransport("truck", 7)).not.toThrow();
    expect(readStoredLoaderTransport(7)).toBeNull();
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
    { product: 12, label: "Д1с · Красный 50 кг", color: "Red", bags: 20, short: { "2": 0 } },
    { product: 15, label: "Б · Синий 25 кг", color: "Blue", bags: 40, short: {} },
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
