export interface PermissionPreset {
  key: string;
  label: string;
  codes: string[];
}

/** Шаблоны ролей: ставят галочки в один клик, дальше права правятся вручную. */
export const PERMISSION_PRESETS: PermissionPreset[] = [
  {
    key: "cashier",
    label: "Кассир",
    codes: [
      "payments.view",
      "payments.create",
      "payments.confirm",
      "reports.view",
      "clients.view",
      "stores.view",
      "orders.view",
    ],
  },
  {
    key: "accountant",
    label: "Бухгалтер",
    codes: [
      "reports.view",
      "reports.export",
      "payments.view",
      "payments.confirm",
      "clients.view",
      "stores.view",
      "orders.view",
    ],
  },
  {
    key: "manager",
    label: "Менеджер",
    codes: [
      "orders.view",
      "orders.create",
      "orders.edit",
      "orders.confirm",
      "orders.correct_price",
      "clients.view",
      "clients.create",
      "clients.edit",
      "clients.set_price",
      "clients.manage_access",
      "stores.view",
      "stores.create",
      "stores.edit",
      "catalog.view",
      "warehouse.view",
    ],
  },
  // Фуры и вагоны грузят разные люди: область — отдельное право (loader.trucks / loader.wagons).
  {
    key: "loader_trucks",
    label: "Грузчик: фуры",
    codes: ["loader.view", "loader.confirm", "loader.trucks", "monoblock.view"],
  },
  {
    key: "loader_wagons",
    label: "Грузчик: вагоны",
    codes: ["loader.view", "loader.confirm", "loader.wagons", "monoblock.view"],
  },
  { key: "weigher", label: "Весовщик", codes: ["grain.view", "grain.arrive", "grain.weigh", "grain.correct_weighing"] },
  {
    key: "storekeeper",
    label: "Кладовщик",
    codes: ["warehouse.view", "warehouse.adjust", "silos.view", "catalog.view"],
  },
  { key: "observer", label: "Наблюдатель", codes: ["monoblock.view", "orders.view", "reports.view"] },
];

/** Шаблон заменяет выбор; права, которые текущий админ выдать не может, не ставятся. */
export function applyPreset(preset: PermissionPreset, ungrantable: ReadonlySet<string>): Set<string> {
  return new Set(preset.codes.filter((code) => !ungrantable.has(code)));
}
