export interface MarketCountry {
  name: string;
  /** ISO 3166-1 alpha-2 — для флага. */
  iso: string;
  /** Телефонный код страны без «+». */
  dial: string;
  /** Национальная часть номера, «#» — цифра. */
  mask: string;
  /** Внутренний префикс, который пишут по привычке (8 705…, 0555…): номер с него не начинается. */
  trunk?: string;
}

// Основные рынки экспорта Асыл-LTD + соседние страны.
export const MARKET_COUNTRIES: MarketCountry[] = [
  { name: "Казахстан", iso: "KZ", dial: "7", mask: "(###) ###-##-##", trunk: "8" },
  { name: "Узбекистан", iso: "UZ", dial: "998", mask: "## ###-##-##" },
  { name: "Афганистан", iso: "AF", dial: "93", mask: "## ###-####", trunk: "0" },
  { name: "Кыргызстан", iso: "KG", dial: "996", mask: "### ###-###", trunk: "0" },
  { name: "Таджикистан", iso: "TJ", dial: "992", mask: "## ###-####" },
  { name: "Туркменистан", iso: "TM", dial: "993", mask: "## ##-##-##", trunk: "8" },
  { name: "Китай", iso: "CN", dial: "86", mask: "### ####-####", trunk: "0" },
  { name: "Иран", iso: "IR", dial: "98", mask: "### ###-####", trunk: "0" },
  { name: "Россия", iso: "RU", dial: "7", mask: "(###) ###-##-##" },
  { name: "Азербайджан", iso: "AZ", dial: "994", mask: "## ###-##-##", trunk: "0" },
  { name: "Грузия", iso: "GE", dial: "995", mask: "### ##-##-##" },
  { name: "Монголия", iso: "MN", dial: "976", mask: "####-####" },
];

export const OTHER_COUNTRY = "Другая";

export const COUNTRIES: string[] = [...MARKET_COUNTRIES.map((country) => country.name), OTHER_COUNTRY];

export function findCountry(name: string | null | undefined): MarketCountry | null {
  return MARKET_COUNTRIES.find((country) => country.name === name) ?? null;
}

export function countryFlag(iso: string): string {
  return String.fromCodePoint(...[...iso.toUpperCase()].map((char) => 0x1f1e6 + char.charCodeAt(0) - 65));
}
