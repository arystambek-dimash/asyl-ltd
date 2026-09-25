import { readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import type { Department, Me } from "@/lib/types";

/** «Все отделы» в переключателе кассы — параметр department не отправляется. */
export const ALL_DEPARTMENTS = "all";
const DEPARTMENT_STORAGE_KEY = "asyl_cashier_department";

export interface DepartmentScope {
  /** Отдел из карточки сотрудника: сервер сам режет данные — переключать нечего. */
  assigned: NonNullable<Me["sales_department"]> | null;
}

/** Кто видит переключатель отделов: /auth/me/ отдаёт отдел только тому, кого сервер им ограничивает. */
export function departmentScope(me: Me | null): DepartmentScope {
  return { assigned: me?.sales_department ?? null };
}

/** Подпись выбранной кассы для шапки: закреплённый отдел, «Все отделы» или отдел по коду. */
export function scopeLabel(
  code: string,
  departments: readonly Department[],
  assigned: DepartmentScope["assigned"],
): { name: string; color: string | null } {
  if (assigned) return { name: assigned.name, color: assigned.color };
  if (code === ALL_DEPARTMENTS) return { name: "Все отделы", color: null };
  const department = departments.find((row) => row.code === code);
  return department ? { name: department.name, color: department.color } : { name: "Отдел", color: null };
}

/**
 * Отдел запросов оплат к подтверждению. Очередь общая для всех отделов: закреплённый отдел её не сужает
 * (null — все отделы); сужает только отдел, выбранный в шапке сотрудником без закрепления.
 */
export function queueDepartment(scope: DepartmentScope, chosen: string): string | null {
  return scope.assigned || chosen === ALL_DEPARTMENTS ? null : chosen;
}

/** Карточка заказа из очереди открывается только его отделу — очередь общая, а заказы нет. */
export function canOpenQueueOrder(assigned: DepartmentScope["assigned"], orderDepartment: string): boolean {
  return !assigned || assigned.code === orderDepartment;
}

export function cashierName(me: Me | null): string {
  if (!me) return "";
  return [me.first_name, me.last_name].filter(Boolean).join(" ").trim() || me.username;
}

/** Отдел, выбранный в шапке кассы: на общем телефоне у каждого свой. */
export function readStoredDepartment(userId?: number): string | null {
  return readStoredChoice(userChoiceKey(DEPARTMENT_STORAGE_KEY, userId));
}

export function storeDepartment(code: string, userId?: number) {
  storeChoice(userChoiceKey(DEPARTMENT_STORAGE_KEY, userId), code);
}
