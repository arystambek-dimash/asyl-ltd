import { afterEach, describe, expect, it, vi } from "vitest";
import { loadWeight, readStoredLoaderTransport, storeLoaderTransport } from "./loader";

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
