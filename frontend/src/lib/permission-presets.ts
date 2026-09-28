export interface PermissionPreset {
  key: string;
  label: string;
  codes: string[];
}

/** Главная и «Задачи»: есть в каждом шаблоне и у нового сотрудника, скрыть — снять галочку. */
const EVERYDAY_CODES = ["dashboard.view", "tasks.own"];

/** Шаблоны ролей: ставят галочки в один клик, дальше права правятся вручную. */
export const PERMISSION_PRESETS: PermissionPreset[] = [
  {
    key: "cashier",
    label: "Кассир",
    codes: [
      ...EVERYDAY_CODES,
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
      ...EVERYDAY_CODES,
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
      ...EVERYDAY_CODES,
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
    codes: [...EVERYDAY_CODES, "loader.view", "loader.confirm", "loader.trucks", "monoblock.view"],
  },
  {
    key: "loader_wagons",
    label: "Грузчик: вагоны",
    codes: [...EVERYDAY_CODES, "loader.view", "loader.confirm", "loader.wagons", "monoblock.view"],
  },
  {
    key: "weigher",
    label: "Весовщик",
    codes: [...EVERYDAY_CODES, "grain.view", "grain.arrive", "grain.weigh", "grain.correct_weighing"],
  },
  {
    key: "storekeeper",
    label: "Кладовщик",
    codes: [...EVERYDAY_CODES, "warehouse.view", "warehouse.adjust", "silos.view", "catalog.view"],
  },
  {
    key: "observer",
    label: "Наблюдатель",
    codes: [...EVERYDAY_CODES, "monoblock.view", "orders.view", "reports.view"],
  },
];

function grantable(codes: readonly string[], ungrantable: ReadonlySet<string>): Set<string> {
  return new Set(codes.filter((code) => !ungrantable.has(code)));
}

/** Шаблон заменяет выбор; права, которые текущий админ выдать не может, не ставятся. */
export function applyPreset(preset: PermissionPreset, ungrantable: ReadonlySet<string>): Set<string> {
  return grantable(preset.codes, ungrantable);
}

/** Права нового сотрудника до выбора шаблона. */
export function newEmployeePermissions(ungrantable: ReadonlySet<string>): Set<string> {
  return grantable(EVERYDAY_CODES, ungrantable);
}
