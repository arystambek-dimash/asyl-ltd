/** Порядок разделов = меню (как `SECTION_ORDER` в backend/apps/sys_permissions/perms.py). */
export const SECTION_ORDER = [
  "reports",
  "orders",
  "payments",
  "monoblock",
  "loader",
  "warehouse",
  "silos",
  "grain",
  "clients",
  "stores",
  "catalog",
  "tasks",
  "events",
  "employees",
  "sys_permissions",
];

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
  { key: "loader", label: "Грузчик", codes: ["loader.view", "loader.confirm", "monoblock.view"] },
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

/** Индекс раздела в порядке меню; неизвестные разделы — в конце. */
export function sectionRank(section: string): number {
  const index = SECTION_ORDER.indexOf(section);
  return index === -1 ? SECTION_ORDER.length : index;
}
