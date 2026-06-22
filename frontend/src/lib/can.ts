import type { Me } from "@/lib/types";

export function can(me: Me | null, code: string): boolean {
  if (!me) return false;
  if (me.is_superuser) return true;
  return me.permissions.includes(code);
}
