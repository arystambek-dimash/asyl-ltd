export function DepartmentBadge({ name, color }: { name?: string; color?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold">
      <span className="size-2 rounded-full" style={{ backgroundColor: color ?? "#64748B" }} />
      {name || "Нет отдела"}
    </span>
  );
}
