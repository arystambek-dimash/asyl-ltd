import type { Department, Me } from "@/lib/types";

/** «Все отделы» в переключателе кассы — параметр department не отправляется. */
export const ALL_DEPARTMENTS = "all";
export const DEPARTMENT_STORAGE_KEY = "asyl_cashier_department";

export interface DepartmentScope {
  /** Отдел из карточки сотрудника: сервер сам режет данные — переключать нечего. */
  assigned: NonNullable<Me["sales_department"]> | null;
  switchable: boolean;
}

/** Кто видит переключатель отделов: как на сервере, суперпользователь видит всё даже с отделом в карточке. */
export function departmentScope(me: Me | null): DepartmentScope {
  const assigned = me && !me.is_superuser ? me.sales_department : null;
  return { assigned, switchable: !assigned };
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

export function cashierName(me: Me | null): string {
  if (!me) return "";
  return [me.first_name, me.last_name].filter(Boolean).join(" ").trim() || me.username;
}

/** Ключ на пользователя: на общем телефоне сменщик не должен унаследовать чужой отдел. */
function storageKey(userId?: number): string {
  return userId ? `${DEPARTMENT_STORAGE_KEY}:${userId}` : DEPARTMENT_STORAGE_KEY;
}

export function readStoredDepartment(userId?: number): string | null {
  try {
    return localStorage.getItem(storageKey(userId));
  } catch {
    return null;
  }
}

export function storeDepartment(code: string, userId?: number) {
  try {
    localStorage.setItem(storageKey(userId), code);
  } catch {
    // Приватный режим или запрет хранилища — выбор живёт до перезагрузки.
  }
}
