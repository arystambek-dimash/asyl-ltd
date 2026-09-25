import { formatPlatePair } from "@/lib/plates";
import type { ShipmentWagon } from "@/lib/types";
import { formatTons, pluralRu } from "@/lib/utils";

export const wagonsWord = (count: number) => pluralRu(count, ["вагон", "вагона", "вагонов"]);

/** «12 вагонов · ст. Раустан» — отгрузка по отчёту о вагонах одной строкой. */
export function wagonsHeadline(count: number, station = ""): string {
  return `${count} ${wagonsWord(count)}` + (station ? ` · ст. ${station}` : "");
}

/** Транспорт заказа, как его видно в списках и карточках. */
export interface OrderTransport {
  transport_type?: "truck" | "train";
  truck_number: string;
  trailer_number?: string;
  rail_station?: string;
  wagons?: readonly ShipmentWagon[];
}

/**
 * Номер транспорта заказа одной строкой: пара фуры, номер вагона или
 * «12 вагонов · ст. Раустан» у отгрузки по отчёту. Пусто — номера нет.
 */
export function orderTransportText(order: OrderTransport): string {
  if (order.transport_type === "train") {
    if (order.wagons?.length) return wagonsHeadline(order.wagons.length, order.rail_station);
    return order.truck_number;
  }
  return formatPlatePair(order.truck_number, order.trailer_number ?? "");
}

/**
 * Транспорт в карточке заказа: «Вагон 00123456», «Вагон · без номера»,
 * «12 вагонов · ст. Раустан» или номер фуры; ``emptyTruck`` — фура без номера.
 */
export function orderTransportLabel(order: OrderTransport, emptyTruck: string): string {
  if (order.transport_type === "train" && !order.wagons?.length) return `Вагон ${order.truck_number || "· без номера"}`;
  return orderTransportText(order) || emptyTruck;
}

/** Вес вагона в тоннах: «68», «67,5». */
export function wagonTons(wagon: Pick<ShipmentWagon, "weight_kg">): string {
  return formatTons(wagon.weight_kg);
}
