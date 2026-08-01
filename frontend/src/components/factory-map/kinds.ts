import {
  Beaker,
  Boxes,
  Camera,
  Car,
  Factory,
  Fence,
  Route,
  Ruler,
  Scale,
  Soup,
  TrainFront,
  Truck,
  Users,
  Warehouse,
  type LucideIcon,
} from "lucide-react";
import type { FactoryZoneKind } from "@/lib/types";

/** Категории легенды — как на эскизе карты. */
export type ZoneCategory = "storage" | "production" | "logistics" | "cameras" | "infra";

export const CATEGORIES: Record<ZoneCategory, { label: string; color: string }> = {
  storage: { label: "Хранение", color: "#2563eb" },
  production: { label: "Производство", color: "#16a34a" },
  logistics: { label: "Логистика", color: "#d97706" },
  cameras: { label: "Камеры CV", color: "#8b5cf6" },
  infra: { label: "Инфраструктура", color: "#64748b" },
};

export type KindConfig = {
  label: string;
  defaultName: string;
  color: string;
  icon: LucideIcon;
  category: ZoneCategory;
  /** Размер нового участка при добавлении из палитры. */
  defaultSize: { width: number; height: number };
};

export const KINDS: Record<FactoryZoneKind, KindConfig> = {
  silos: {
    label: "Цистерны зерна",
    defaultName: "Цистерны хранения зерна",
    color: "#A66A20",
    icon: Warehouse,
    category: "storage",
    defaultSize: { width: 216, height: 196 },
  },
  warehouse: {
    label: "Склад продукции",
    defaultName: "Склад готовой продукции",
    color: "#4E6B55",
    icon: Boxes,
    category: "storage",
    defaultSize: { width: 300, height: 330 },
  },
  production: {
    label: "Мельница",
    defaultName: "Мельница · Производство",
    color: "#2e7d4f",
    icon: Factory,
    category: "production",
    defaultSize: { width: 250, height: 168 },
  },
  lab: {
    label: "Лаборатория",
    defaultName: "Лаборатория",
    color: "#6E5B84",
    icon: Beaker,
    category: "production",
    defaultSize: { width: 170, height: 120 },
  },
  gate: {
    label: "КПП",
    defaultName: "КПП · охрана",
    color: "#C58A35",
    icon: Fence,
    category: "logistics",
    defaultSize: { width: 96, height: 72 },
  },
  scale: {
    label: "Автовесы",
    defaultName: "Автовесы CAS",
    color: "#3D7187",
    icon: Scale,
    category: "logistics",
    defaultSize: { width: 252, height: 96 },
  },
  dock: {
    label: "Пост погрузки",
    defaultName: "Пост погрузки",
    color: "#C58A35",
    icon: Truck,
    category: "logistics",
    defaultSize: { width: 190, height: 104 },
  },
  conveyor: {
    label: "Конвейер",
    defaultName: "Конвейер → вагон",
    color: "#59636B",
    icon: Route,
    category: "logistics",
    defaultSize: { width: 96, height: 214 },
  },
  rail: {
    label: "Ж/д путь",
    defaultName: "Железнодорожный путь",
    color: "#59636B",
    icon: TrainFront,
    category: "logistics",
    defaultSize: { width: 320, height: 56 },
  },
  parking: {
    label: "Парковка",
    defaultName: "Парковка сотрудников",
    color: "#697386",
    icon: Car,
    category: "infra",
    defaultSize: { width: 180, height: 92 },
  },
  office: {
    label: "Офис",
    defaultName: "Офис",
    color: "#315D74",
    icon: Users,
    category: "infra",
    defaultSize: { width: 204, height: 132 },
  },
  canteen: {
    label: "Столовая",
    defaultName: "Столовая",
    color: "#C58A35",
    icon: Soup,
    category: "infra",
    defaultSize: { width: 176, height: 112 },
  },
  utility: {
    label: "Служебный",
    defaultName: "Служебный участок",
    color: "#697386",
    icon: Ruler,
    category: "infra",
    defaultSize: { width: 140, height: 100 },
  },
  camera: {
    label: "Камера",
    defaultName: "Камера",
    color: "#6D28D9",
    icon: Camera,
    category: "cameras",
    defaultSize: { width: 48, height: 48 },
  },
};

export const KIND_ROWS = Object.entries(KINDS) as [FactoryZoneKind, KindConfig][];
