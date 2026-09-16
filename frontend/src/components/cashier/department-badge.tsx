import type { Order } from "@/lib/types";

// Янтарный: заявка клиента без отдела ждёт, пока отдел заберёт её себе.
const WAITING_COLOR = "#D97706";

export function DepartmentBadge({ name, color }: { name?: string; color?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold">
      <span className="size-2 rounded-full" style={{ backgroundColor: color ?? "#64748B" }} />
      {name || "Нет отдела"}
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
