import type { Me } from "@/lib/types";

export function can(me: Me | null, code: string): boolean {
  if (!me) return false;
  if (me.is_superuser) return true;
  return (me.permissions ?? []).includes(code);
}

/** Право или набор прав, где достаточно ЛЮБОГО из них. */
export type Perm = string | readonly string[];

export function canAny(me: Me | null, perm: Perm): boolean {
  return (typeof perm === "string" ? [perm] : perm).some((code) => can(me, code));
}

/** Страница «Задачи»: свои задачи, чужие или постановка — как TASK_PAGE_PERMS в backend/apps/tasks/services.py. */
export const TASKS_ENTRY_PERMS = ["tasks.own", "tasks.view", "tasks.create"] as const;
