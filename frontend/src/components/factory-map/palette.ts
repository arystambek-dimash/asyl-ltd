/**
 * Общая палитра «живой карты территории».
 *
 * Все ассеты (assets/*.tsx) рисуются этими цветами, чтобы сцена читалась
 * как единая иллюстрация. Меняя значения здесь, вы перекрашиваете всю карту.
 */
export const MAP_PALETTE = {
  grass: "#e3ead9",
  grassDark: "#d7e0c9",
  asphalt: "#c9ced6",
  asphaltDark: "#aab1bc",
  roadLine: "#ffffff",
  border: "#dfe3ea",
  steelDark: "#c9cfd8",
  grainTop: "#f0c860",
  grainBottom: "#d9a83f",
  bag: "#f2e2c4",
  bagAlt: "#f7ecd2",
  bagStroke: "#d8c294",
  wood: "#a9825a",
  woodDark: "#8a6a48",
  text: "#1c1f24",
  textMuted: "#6b7280",
  cameraBody: "#6d28d9",
  cameraHalo: "#8b5cf6",
  online: "#16a34a",
  warning: "#d97706",
} as const;

/** Идентификаторы общих градиентов/фильтров из <FactoryMapDefs>. */
export const DEFS = {
  siloBody: "fm-silo-body",
  grain: "fm-grain",
  roof: "fm-roof",
  mill: "fm-mill",
  soft: "fm-soft",
} as const;
