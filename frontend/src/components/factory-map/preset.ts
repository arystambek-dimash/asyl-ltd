import type { FactoryZone } from "@/lib/types";

/**
 * Пресет «Как на эскизе» — раскладка территории из утверждённого макета.
 * Зеркало backend/apps/warehouse/models.py::default_factory_zones — меняйте синхронно.
 */
export const PRESET_TITLE = "Территория комплекса";

export const PRESET_ZONES: FactoryZone[] = [
  { id: "gate", name: "КПП · охрана", kind: "gate", x: 42, y: 264, width: 96, height: 72, color: "#C58A35", note: "Шлагбаум и распознавание номеров" },
  { id: "parking", name: "Парковка сотрудников", kind: "parking", x: 92, y: 96, width: 180, height: 92, color: "#697386", note: "Личный транспорт" },
  { id: "silo-park", name: "Цистерны хранения зерна", kind: "silos", x: 268, y: 88, width: 216, height: 196, color: "#A66A20", note: "Живые остатки из силосного парка" },
  { id: "mill", name: "Мельница · Производство", kind: "production", x: 532, y: 96, width: 250, height: 168, color: "#4E6B55", note: "Фасовка и робот KUKA" },
  { id: "truck-scale", name: "Автовесы CAS", kind: "scale", x: 336, y: 352, width: 252, height: 96, color: "#3D7187", note: "Только отруби (насыпь)" },
  { id: "dock", name: "Пост погрузки", kind: "dock", x: 648, y: 380, width: 190, height: 104, color: "#C58A35", note: "Автотранспорт · CV-подсчёт" },
  { id: "warehouse", name: "Склад готовой продукции", kind: "warehouse", x: 860, y: 268, width: 300, height: 330, color: "#4E6B55", note: "Остатки мешков по сортам" },
  { id: "conveyor", name: "Конвейер → вагон", kind: "conveyor", x: 906, y: 58, width: 96, height: 214, color: "#59636B", note: "Подача мешков на погрузку" },
  { id: "canteen", name: "Столовая · 70 мест", kind: "canteen", x: 110, y: 432, width: 176, height: 112, color: "#C58A35", note: "Кухня · обеды для сотрудников" },
  { id: "office", name: "Офис", kind: "office", x: 336, y: 486, width: 204, height: 132, color: "#315D74", note: "Директор · Бухгалтер · Экран видеонаблюдения" },
  { id: "cam-gate", name: "Камера · КПП", kind: "camera", x: 150, y: 212, width: 48, height: 48, color: "#6D28D9", note: "" },
  { id: "cam-scale", name: "Камера · Автовесы", kind: "camera", x: 612, y: 360, width: 48, height: 48, color: "#6D28D9", note: "" },
  { id: "cam-silo", name: "Камера · Цистерны", kind: "camera", x: 494, y: 76, width: 48, height: 48, color: "#6D28D9", note: "" },
  { id: "cam-mill", name: "Камера · Производство", kind: "camera", x: 794, y: 108, width: 48, height: 48, color: "#6D28D9", note: "" },
  { id: "cam-dock", name: "Камера · Пост погрузки", kind: "camera", x: 848, y: 430, width: 48, height: 48, color: "#6D28D9", note: "" },
  { id: "cam-warehouse", name: "Камера · Склад", kind: "camera", x: 1132, y: 220, width: 48, height: 48, color: "#6D28D9", note: "" },
];
