import { TrainFront } from "lucide-react";
import { countryFlag } from "@/lib/countries";
import { detectPlateCountry, formatPlate, type PlateCountry } from "@/lib/plates";
import { cn } from "@/lib/utils";
import { orderTransportText, type OrderTransport } from "@/lib/wagons";

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
function PlatePair({ truck, trailer = "", size = "md" }: { truck: string; trailer?: string; size?: "md" | "lg" }) {
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

/**
 * Транспорт заказа крупно: у фуры — тягач и прицеп табличками, как на самих
 * машинах; у вагона — номер или «12 вагонов · ст. Раустан» у отгрузки по отчёту.
 */
export function OrderTransportBadge({ order, size = "md" }: { order: OrderTransport; size?: "md" | "lg" }) {
  const lg = size === "lg";
  if (order.transport_type === "train") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border-2 border-neutral-800 bg-white px-2 py-1 font-bold tabular-nums text-neutral-900",
          lg ? "text-xl" : "text-sm",
        )}
      >
        <TrainFront className={lg ? "size-5" : "size-4"} />
        {orderTransportText(order) || "без номера"}
      </span>
    );
  }
  if (!order.truck_number) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-md border-2 border-dashed border-[var(--border)] px-2 py-1 font-semibold text-[var(--muted-foreground)]",
          lg ? "text-lg" : "text-sm",
        )}
      >
        Без номера
      </span>
    );
  }
  return <PlatePair truck={order.truck_number} trailer={order.trailer_number} size={size} />;
}
