import { Badge } from "@/components/ui/badge";
import { formatPlate, PlateBadge } from "@/components/ui/license-plate-input";
import type { Order } from "@/lib/types";

type TransportType = Order["transport_type"];

/** Wagon numbers are identifiers: preserve all eight digits and leading zeros. */
export function formatTransportNumber(value: string, transportType: TransportType): string {
  return transportType === "truck" ? formatPlate(value) : value;
}

export function TransportNumberBadge({ value, transportType }: { value: string; transportType: TransportType }) {
  if (transportType === "train") {
    return <Badge tone="outline">{value ? `Вагон ${value}` : "Вагон · без номера"}</Badge>;
  }
  if (!transportType) return <span className="font-medium tabular-nums">{value || "Без номера"}</span>;
  return value ? <PlateBadge value={value} /> : <Badge tone="muted">Без номера</Badge>;
}
