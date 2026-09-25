import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

// Янтарный: заявка клиента без отдела ждёт, пока отдел заберёт её себе.
const WAITING_COLOR = "#D97706";

/** Цветная точка отдела; без цвета — нейтральная серая. Размер задаёт className. */
export function DepartmentDot({ color, className }: { color?: string | null; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("size-2.5 shrink-0 rounded-full", !color && "bg-[var(--muted-foreground)]", className)}
      style={color ? { backgroundColor: color } : undefined}
    />
  );
}

export function DepartmentBadge({
  name,
  color,
  className,
}: {
  name?: string | null;
  color?: string | null;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold",
        className,
      )}
    >
      <DepartmentDot color={color} className="size-2" />
      <span className="truncate">{name || "Нет отдела"}</span>
    </span>
  );
}

/** Отдел заявки: свой, иначе отдел клиента, иначе «Ждёт отдела». */
export function OrderDepartmentBadge({ order }: { order: Order }) {
  if (order.department) {
    return <DepartmentBadge name={order.department_name || order.department} color={order.department_color} />;
  }
  if (order.client_department) {
    return <DepartmentBadge name={order.client_department_name || order.client_department} />;
  }
  return <DepartmentBadge name="Ждёт отдела" color={WAITING_COLOR} />;
}
