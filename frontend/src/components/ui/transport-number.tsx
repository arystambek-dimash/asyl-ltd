import { Badge } from "@/components/ui/badge";
import { countryFlag } from "@/lib/countries";
import { detectPlateCountry, formatPlate, formatPlatePair, type PlateCountry } from "@/lib/plates";
import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

type TransportType = Order["transport_type"];

/** Wagon numbers are identifiers: preserve all eight digits and leading zeros. */
export function formatTransportNumber(value: string, transportType: TransportType, trailer = ""): string {
  return transportType === "truck" ? formatPlatePair(value, trailer) : value;
}

// Полоса страны слева на табличке — цвет флага страны.
const STRIP_COLORS: Record<PlateCountry, string> = {
  KZ: "bg-[#0057b8]",
  KG: "bg-[#c8102e]",
  UZ: "bg-[#1eb53a]",
  RU: "bg-[#0039a6]",
};

/** Госномер как знак: белая табличка с полосой и флагом страны. Неоднозначный номер — без флага. */
export function PlateBadge({
  value,
  size = "md",
  className,
}: {
  value: string;
  size?: "md" | "lg";
  className?: string;
}) {
  const text = formatPlate(value);
  if (!text) return <span className={cn("font-semibold tabular-nums", className)}>—</span>;
  const country = detectPlateCountry(value);
  const lg = size === "lg";
  return (
    <span
      className={cn(
        "inline-flex select-none items-stretch overflow-hidden rounded-md border-2 border-neutral-800 bg-white font-bold tabular-nums text-neutral-900 shadow-sm",
        className,
      )}
    >
      {country && (
        <span
          className={cn(
            "flex flex-col items-center justify-center gap-0.5 px-1 leading-none text-white",
            STRIP_COLORS[country],
          )}
        >
          <span aria-hidden className={lg ? "text-sm" : "text-[10px]"}>
            {countryFlag(country)}
          </span>
          <span className={lg ? "text-[10px]" : "text-[8px]"}>{country}</span>
        </span>
      )}
      <span className={cn("flex items-center whitespace-nowrap tracking-wider", lg ? "px-3 text-2xl" : "px-2 text-sm")}>
        {text}
      </span>
    </span>
  );
}

/** Тягач и прицеп табличками: «[07 KG 695 ADT] / [07 KG 837 PB]». */
export function PlatePair({
  truck,
  trailer = "",
  size = "md",
}: {
  truck: string;
  trailer?: string;
  size?: "md" | "lg";
}) {
  if (!trailer) return <PlateBadge value={truck} size={size} />;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <PlateBadge value={truck} size={size} />
      <span aria-hidden className="text-[var(--muted-foreground)]">
        /
      </span>
      <PlateBadge value={trailer} size={size} />
    </span>
  );
}

export function TransportNumberBadge({
  value,
  transportType,
  trailer = "",
}: {
  value: string;
  transportType: TransportType;
  trailer?: string;
}) {
  if (transportType === "train") {
    return <Badge tone="outline">{value ? `Вагон ${value}` : "Вагон · без номера"}</Badge>;
  }
  if (!transportType) return <span className="font-medium tabular-nums">{value || "Без номера"}</span>;
  return value || trailer ? <PlatePair truck={value} trailer={trailer} /> : <Badge tone="muted">Без номера</Badge>;
}
