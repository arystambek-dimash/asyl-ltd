import type { Me } from "@/lib/types";

export function can(me: Me | null, code: string): boolean {
  if (!me) return false;
  if (me.is_superuser) return true;
  return me.permissions.includes(code);
}

/** Менеджер выездного отдела: работает только в разделе «Сити». */
export function isDept2Only(me: Me | null): boolean {
  return !!me && !me.is_superuser && can(me, "dept2.view") && !can(me, "orders.view");
}

/** Домашняя страница пользователя после входа. */
export function homeFor(me: Me | null): string {
  if (!me) return "/login";
  if (me.is_client) return "/portal/catalog";
  if (isDept2Only(me)) return "/city/orders";
  return "/dashboard";
}
