import { describe, expect, it } from "vitest";
import { orderTransportLabel, orderTransportText, wagonsHeadline, wagonTons } from "./wagons";

const wagon = (number: string, weight_kg = "68000.00") => ({ number, product_label: "Д1", bags: 1360, weight_kg });

describe("wagons", () => {
  it("склоняет вагоны и добавляет станцию", () => {
    expect(wagonsHeadline(1)).toBe("1 вагон");
    expect(wagonsHeadline(3, "Раустан")).toBe("3 вагона · ст. Раустан");
    expect(wagonsHeadline(12, "Раустан")).toBe("12 вагонов · ст. Раустан");
  });

  it("показывает транспорт заказа одной строкой", () => {
    expect(
      orderTransportText({
        transport_type: "train",
        truck_number: "",
        rail_station: "Раустан",
        wagons: [wagon("28087658"), wagon("28087666")],
      }),
    ).toBe("2 вагона · ст. Раустан");
    expect(orderTransportText({ transport_type: "train", truck_number: "00123456", wagons: [] })).toBe("00123456");
    expect(orderTransportText({ transport_type: "train", truck_number: "" })).toBe("");
    expect(
      orderTransportText({ transport_type: "truck", truck_number: "07KG695ADT", trailer_number: "07KG837PB" }),
    ).toBe("07 KG 695 ADT / 07 KG 837 PB");
  });

  it("подписывает транспорт в карточке заказа", () => {
    expect(orderTransportLabel({ transport_type: "train", truck_number: "00123456" }, "Машина")).toBe("Вагон 00123456");
    expect(orderTransportLabel({ transport_type: "train", truck_number: "" }, "Машина")).toBe("Вагон · без номера");
    expect(
      orderTransportLabel({ transport_type: "train", truck_number: "", wagons: [wagon("28087658")] }, "Машина"),
    ).toBe("1 вагон");
    expect(orderTransportLabel({ transport_type: "truck", truck_number: "" }, "Машина не указана")).toBe(
      "Машина не указана",
    );
  });

  it("переводит вес вагона в тонны", () => {
    expect(wagonTons(wagon("1", "68000.00"))).toBe("68");
    expect(wagonTons(wagon("1", "67500.00"))).toBe("67,5");
  });
});
