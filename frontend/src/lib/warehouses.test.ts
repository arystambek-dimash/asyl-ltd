import { expect, it } from "vitest";
import { pickWarehouse } from "@/lib/warehouses";
import type { Warehouse } from "@/lib/types";

const warehouse = (id: number, fields: Partial<Warehouse> = {}): Warehouse => ({
  id,
  code: `wh-${id}`,
  name: `Склад ${id}`,
  address: "",
  is_active: true,
  is_default: false,
  ...fields,
});

it("берёт выбранный склад, иначе основной, иначе первый активный", () => {
  const list = [warehouse(1, { is_active: false }), warehouse(2), warehouse(3, { is_default: true })];

  expect(pickWarehouse(list, "2")?.id).toBe(2);
  expect(pickWarehouse(list, null)?.id).toBe(3);
  expect(pickWarehouse(list, "1")?.id).toBe(3);
  expect(pickWarehouse([warehouse(4), warehouse(5)], "99")?.id).toBe(4);
  expect(pickWarehouse([], null)).toBeNull();
});
