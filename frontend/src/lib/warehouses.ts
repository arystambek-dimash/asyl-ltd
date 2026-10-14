import type { Warehouse } from "@/lib/types";

/**
 * Склад экрана: выбранный пользователем, иначе основной, иначе первый.
 * Отключённые склады не предлагаются. Одно правило для «Склада» и «Главной».
 */
export function pickWarehouse(warehouses: readonly Warehouse[], requestedId: string | null): Warehouse | null {
  const active = warehouses.filter((item) => item.is_active);
  const requested = active.find((item) => String(item.id) === requestedId);
  return requested ?? active.find((item) => item.is_default) ?? active[0] ?? null;
}
