import type { Me } from "@/lib/types";

export function can(me: Me | null, code: string): boolean {
  if (!me) return false;
  if (me.is_superuser) return true;
  return me.permissions.includes(code);
}

/** Домашняя страница пользователя после входа. */
export function homeFor(me: Me | null): string {
  if (!me) return "/login";
  if (me.is_client) return "/portal/catalog";
  if (me.is_monoblock) return "/monoblock";
  return "/dashboard";
}
