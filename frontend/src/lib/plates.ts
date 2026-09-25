import { findCountry } from "@/lib/countries";

/*
  Номера машин (тягач и полуприцеп) — зеркало backend/apps/common/plates.py.

  Номер хранится слитно, латиницей и заглавными (07KG695ADT). Страну по номеру
  определяем только для отображения: формат, не похожий на известный, — это
  предупреждение, а не ошибка. Оба модуля проходят одни и те же тест-векторы
  (backend/apps/common/tests/plate_vectors.json); поиск по номеру — только на бэкенде.
*/

export type PlateKind = "truck" | "trailer";

/** Пара номеров фуры, как её отдаёт и принимает API. */
export interface TransportPair {
  truck_number: string;
  trailer_number: string;
}

/** Страны с известным форматом номера; у остальных («Другая») формат свободный. */
export const PLATE_COUNTRIES = ["KZ", "KG", "UZ", "RU"] as const;
export type PlateCountry = (typeof PLATE_COUNTRIES)[number];

// Кириллические буквы, которые на номере выглядят как латинские.
const CYRILLIC_TWINS: Record<string, string> = {
  А: "A",
  В: "B",
  Е: "E",
  К: "K",
  М: "M",
  Н: "H",
  О: "O",
  Р: "P",
  С: "C",
  Т: "T",
  У: "Y",
  Х: "X",
};
// Пробелы (в том числе неразрывные), дефисы, точки и «·» — только оформление.
const SEPARATORS = /[\s\-.·]+/g;
const PLATE_RE = /^[0-9A-Z]{4,12}$/;
// Номер вагона — прежнее правило бэкенда (orders/transport.py, _WAGON_NUMBER_RE).
const WAGON_RE = /^[0-9]{8}$/;
const MAX_PLATE_LENGTH = 12;

const RU_LETTERS = "[ABEKMHOPCTYX]";
const FORMATS: [PlateCountry, RegExp][] = [
  ["KZ", /^(\d{3})([A-Z]{3})(\d{2})$/],
  ["KZ", /^(\d{3})([A-Z]{2})(\d{2})$/],
  ["KZ", /^([A-Z])(\d{3})([A-Z]{2,3})$/],
  ["KG", /^(\d{2})(KG)(\d{3})([A-Z]{2,3})$/],
  ["KG", /^([A-Z])(\d{4})([A-Z]{1,2})$/],
  ["UZ", /^(\d{2})([A-Z])(\d{3})([A-Z]{2})$/],
  ["UZ", /^(\d{2})(\d{3})([A-Z]{3})$/],
  ["RU", new RegExp(`^(${RU_LETTERS})(\\d{3})(${RU_LETTERS}{2})(\\d{2,3})$`)],
  ["RU", new RegExp(`^(${RU_LETTERS}{2})(\\d{4})(\\d{2,3})$`)],
  // Киргизский номер, набранный без «KG» (07695ADT). Под него же подходит
  // номер юрлица Узбекистана — такой номер страну не показывает.
  ["KG", /^(\d{2})(\d{3})([A-Z]{2,3})$/],
];
const KG_INSERT = /^(\d{2})KG(?=\d)/;
// Отметка страны рядом с номером: «KZ» спереди, «UZ»/«RUS» сзади.
const COUNTRY_MARKERS: [string, string, PlateCountry][] = [
  ["KZ", "", "KZ"],
  ["", "RUS", "RU"],
  ["", "UZ", "UZ"],
];

/** Слитная запись номера: заглавные латинские буквы и цифры. «KG» и O/0 не трогает. */
export function normalizePlate(raw: string | null | undefined): string {
  return String(raw ?? "")
    .trim()
    .toUpperCase()
    .replace(/[АВЕКМНОРСТУХ]/g, (char) => CYRILLIC_TWINS[char])
    .replace(SEPARATORS, "");
}

function matches(compact: string): [PlateCountry, RegExpMatchArray][] {
  return FORMATS.flatMap(([country, pattern]) => {
    const match = compact.match(pattern);
    return match ? [[country, match] as [PlateCountry, RegExpMatchArray]] : [];
  });
}

/** Снять отметку страны, только если без неё остаётся номер этой страны. */
function stripCountryMarker(compact: string): string {
  for (const [prefix, suffix, country] of COUNTRY_MARKERS) {
    if (compact.startsWith(prefix) && compact.endsWith(suffix)) {
      const rest = compact.slice(prefix.length, compact.length - suffix.length);
      if (matches(rest).some(([found]) => found === country)) return rest;
    }
  }
  return compact;
}

/** Ключ сравнения двух записей одного номера (07KG695ADT и 07695ADT — одна машина). */
export function plateMatchKey(compact: string): string {
  let key = normalizePlate(compact);
  if (!matches(key).length) key = stripCountryMarker(key);
  return key.replace(KG_INSERT, "$1");
}

/** ISO-код страны номера — только для флага; неоднозначный и незнакомый номер — null. */
export function detectPlateCountry(compact: string): PlateCountry | null {
  const countries = new Set(matches(normalizePlate(compact)).map(([country]) => country));
  return countries.size === 1 ? [...countries][0] : null;
}

function isKnownPlate(compact: string): boolean {
  return matches(normalizePlate(compact)).length > 0;
}

/** Мягкая проверка: текст предупреждения или null. Прицеп проверяется только жёстким правилом. */
export function plateWarning(compact: string, kind: PlateKind = "truck"): string | null {
  if (!normalizePlate(compact) || kind === "trailer" || isKnownPlate(compact)) return null;
  return "Номер не похож на номера KZ, KG, UZ, RU — проверьте";
}

/** Жёсткое правило бэкенда: 4–12 латинских букв и цифр. */
export function isValidPlate(raw: string): boolean {
  return PLATE_RE.test(normalizePlate(raw));
}

/** Номер вагона: 8 цифр; пробелы и дефисы — оформление. */
export function isValidWagonNumber(raw: string): boolean {
  return WAGON_RE.test(normalizePlate(raw));
}

/** Набор номера вагона: оформление (пробелы, дефисы) отбрасывается, больше 8 знаков не вводится. */
export function typedWagonNumber(raw: string): string {
  return raw.replace(SEPARATORS, "").slice(0, 8);
}

/** Почему API не примет номер (400), или null. Пустой номер — «номера нет», его примут. */
export function transportNumberError(raw: string, transportType: string | undefined): string | null {
  if (!normalizePlate(raw)) return null;
  if (transportType === "train") return isValidWagonNumber(raw) ? null : "Номер вагона: 8 цифр";
  return isValidPlate(raw) ? null : "Номер: от 4 до 12 латинских букв и цифр";
}

/** Номер для людей: «07 KG 695 ADT». Незнакомый текст выводится как есть. */
export function formatPlate(value: string | null | undefined): string {
  const found = matches(normalizePlate(value));
  if (!found.length) return String(value ?? "").trim();
  return found[0][1].slice(1).join(" ");
}

/** Тягач и прицеп одной строкой: «07 KG 695 ADT / 07 KG 837 PB». */
export function formatPlatePair(truck: string, trailer = ""): string {
  const truckText = formatPlate(truck);
  if (!trailer) return truckText;
  return `${truckText || "—"} / ${formatPlate(trailer)}`;
}

/** Уверенность OCR номера в процентах. API отдаёт долю 0…1; значение больше 1 уже в процентах. */
export function formatOcrConfidence(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const confidence = Number(value);
  if (!Number.isFinite(confidence)) return "—";
  return `${Math.round(confidence <= 1 ? confidence * 100 : confidence)}%`;
}

/* ── Поле ввода: маски стран ─────────────────────────────────────────────── */

// «#» — цифра, «@» — буква, остальное — буквы самого номера («KG»), пробел — граница группы.
// Первая подходящая маска задаёт группы и серый хвост подсказки.
const TRUCK_MASKS: Record<PlateCountry, string[]> = {
  KZ: ["### @@@ ##", "### @@ ##", "@ ### @@@", "@ ### @@"],
  KG: ["## KG ### @@@", "## KG ### @@", "@ #### @@", "@ #### @", "## ### @@@", "## ### @@"],
  UZ: ["## @ ### @@", "## ### @@@"],
  RU: ["@ ### @@ ##", "@ ### @@ ###", "@@ #### ##", "@@ #### ###"],
};
const TRAILER_MASKS: Record<PlateCountry, string[]> = {
  KZ: ["### @@ ##", "### @@@ ##", "@ ### @@@", "@ ### @@"],
  KG: ["## KG ### @@", "## KG ### @@@", "@ #### @@", "@ #### @", "## ### @@", "## ### @@@"],
  UZ: TRUCK_MASKS.UZ,
  RU: ["@@ #### ##", "@@ #### ###", "@ ### @@ ##", "@ ### @@ ###"],
};

function slotFits(slot: string, char: string): boolean {
  if (slot === "#") return /\d/.test(char);
  if (slot === "@") return /[A-Z]/.test(char);
  return slot === char;
}

/** Набранное по маске и её недобранный хвост; null — набранное под маску не подходит. */
function fitMask(compact: string, mask: string): { text: string; hint: string } | null {
  let text = "";
  let used = 0;
  let index = 0;
  for (; index < mask.length && used < compact.length; index++) {
    const slot = mask[index];
    if (slot === " ") {
      text += " ";
      continue;
    }
    if (!slotFits(slot, compact[used])) return null;
    text += compact[used++];
  }
  if (used < compact.length) return null;
  const hint = mask.slice(index).replace(/#/g, "0").replace(/@/g, "A");
  return { text, hint };
}

function masksFor(country: PlateCountry, kind: PlateKind): string[] {
  return (kind === "trailer" ? TRAILER_MASKS : TRUCK_MASKS)[country];
}

function fitsCountry(compact: string, country: PlateCountry, kind: PlateKind): boolean {
  return masksFor(country, kind).some((mask) => fitMask(compact, mask));
}

/** Что показать в поле: набранное с пробелами групп и серый хвост маски страны. */
export function plateDisplay(
  compact: string,
  country: PlateCountry | null,
  kind: PlateKind = "truck",
): { text: string; hint: string } {
  for (const mask of country ? masksFor(country, kind) : []) {
    const fitted = fitMask(compact, mask);
    if (fitted) return fitted;
  }
  return { text: formatPlate(compact), hint: "" };
}

/**
 * Страна поля по набранному номеру. Полный номер известной страны выбирает
 * её сам; начало номера оставляет выбранную страну, пока подходит под её
 * маску, иначе — первую подходящую. «Другая» (null) меняется только на полный номер.
 */
export function plateCountryFor(
  compact: string,
  current: PlateCountry | null,
  kind: PlateKind = "truck",
): PlateCountry | null {
  const detected = detectPlateCountry(compact);
  if (detected) return detected;
  if (!current || fitsCountry(compact, current, kind)) return current;
  return PLATE_COUNTRIES.find((country) => fitsCountry(compact, country, kind)) ?? current;
}

/** Набранное в поле: без оформления и знаков, которых в номере не бывает. */
export function readPlateInput(raw: string): string {
  return normalizePlate(raw)
    .replace(/[^0-9A-Z]/g, "")
    .slice(0, MAX_PLATE_LENGTH);
}

/** Страна номера по стране клиента (название из COUNTRIES); не указана — Казахстан. */
export function plateCountryIso(name: string | null | undefined): PlateCountry | null {
  if (!name) return "KZ";
  const iso = findCountry(name)?.iso;
  return PLATE_COUNTRIES.find((country) => country === iso) ?? null;
}

export const EMPTY_TRANSPORT_PAIR: TransportPair = { truck_number: "", trailer_number: "" };
const PAIR_FIELDS = ["truck_number", "trailer_number"] as const;

/** Пара номеров заказа; у заказов старше прицепа и у вагона прицепа нет. */
export function transportPairOf(order: {
  truck_number?: string | null;
  trailer_number?: string | null;
}): TransportPair {
  return { truck_number: order.truck_number ?? "", trailer_number: order.trailer_number ?? "" };
}

/** Одна и та же пара с точностью до записи (пробелы, регистр, «KG»). */
export function sameTransportPair(a: TransportPair, b: TransportPair): boolean {
  return PAIR_FIELDS.every((field) => plateMatchKey(a[field]) === plateMatchKey(b[field]));
}

/** Пара к отправке в API: номера слитно; у вагона прицепа нет — только номер вагона. */
export function transportBody(
  transportType: string | undefined,
  pair: TransportPair,
): Pick<TransportPair, "truck_number"> & Partial<TransportPair> {
  const truck_number = normalizePlate(pair.truck_number);
  return transportType === "train"
    ? { truck_number }
    : { truck_number, trailer_number: normalizePlate(pair.trailer_number) };
}

/**
 * Только исправленные номера пары, слитно. Пустой — «стереть», неисправленный
 * не отправляется: API его не меняет, и устаревший экран не перетрёт чужую правку.
 */
export function transportChanges(saved: TransportPair, next: TransportPair): Partial<TransportPair> {
  const changes: Partial<TransportPair> = {};
  for (const field of PAIR_FIELDS) {
    if (plateMatchKey(saved[field]) !== plateMatchKey(next[field])) changes[field] = normalizePlate(next[field]);
  }
  return changes;
}
