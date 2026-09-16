import { MARKET_COUNTRIES, type MarketCountry } from "@/lib/countries";

/** null — страна не из списка: номер вводится целиком, с кодом. */
export type PhoneCountry = MarketCountry | null;

export interface PhoneParts {
  country: PhoneCountry;
  /** Для страны из списка — цифры без кода страны, для прочих — все цифры с кодом. */
  digits: string;
}

export const DEFAULT_PHONE_COUNTRY = MARKET_COUNTRIES[0];

// E.164: номер вместе с кодом страны — не длиннее 15 цифр.
const MAX_DIGITS = 15;
const MIN_OTHER_DIGITS = 8;

export const onlyDigits = (value: string) => value.replace(/\D/g, "");

export const phoneSlots = (country: MarketCountry) => country.mask.split("#").length - 1;

/** Страна по началу номера с кодом; у общего кода (+7) выигрывает preferred. */
function countryByDial(digits: string, preferred: PhoneCountry, exactLength = false): MarketCountry | null {
  const matches = MARKET_COUNTRIES.filter(
    (country) =>
      digits.startsWith(country.dial) && (!exactLength || digits.length === country.dial.length + phoneSlots(country)),
  );
  if (!matches.length) return null;
  const longest = Math.max(...matches.map((country) => country.dial.length));
  const best = matches.filter((country) => country.dial.length === longest);
  return best.find((country) => country.name === preferred?.name) ?? best[0];
}

/** Срезает то, что пишут по привычке: код страны без «+», 8/0 в начале. */
function fitDigits(country: MarketCountry, digits: string): string {
  const slots = phoneSlots(country);
  let rest = digits;
  if (rest.length > slots && rest.startsWith(country.dial)) rest = rest.slice(country.dial.length);
  else if (country.trunk ? rest.startsWith(country.trunk) : rest.length > slots && /^[08]/.test(rest)) {
    rest = rest.slice(1);
  }
  return rest.slice(0, slots);
}

/** Набранное или вставленное в поле: «+998…» и «998…» целиком переключают страну. */
export function readPhoneInput(raw: string, country: PhoneCountry): PhoneParts {
  const digits = onlyDigits(raw);
  if (!country || raw.trimStart().startsWith("+")) {
    const found = countryByDial(digits, country);
    if (!found) return { country: null, digits: digits.slice(0, MAX_DIGITS) };
    return { country: found, digits: digits.slice(found.dial.length, found.dial.length + phoneSlots(found)) };
  }
  if (digits.length > phoneSlots(country)) {
    const other = countryByDial(digits, country, true);
    if (other && other.dial !== country.dial) return { country: other, digits: digits.slice(other.dial.length) };
  }
  return { country, digits: fitDigits(country, digits) };
}

/** Разбор сохранённого номера, в том числе старых записей без «+» (87055656565). */
export function parsePhone(value: string, preferred: PhoneCountry = DEFAULT_PHONE_COUNTRY): PhoneParts {
  if (!onlyDigits(value)) return { country: preferred, digits: "" };
  const international = value.trimStart().startsWith("+");
  return readPhoneInput(value, international ? preferred : (preferred ?? DEFAULT_PHONE_COUNTRY));
}

/** Цифры по маске; оформление после последней цифры не дописываем, иначе Backspace упрётся в скобку. */
export function formatNational(mask: string, digits: string): string {
  let out = "";
  let used = 0;
  for (const char of mask) {
    if (used === digits.length) break;
    if (char === "#") out += digits[used++];
    else out += char;
  }
  return out;
}

/** Недобранный хвост маски — серая подсказка поверх поля. */
export function maskHint(mask: string, filled: number): string {
  return mask.slice(formatNational(mask, "0".repeat(filled)).length).replace(/#/g, "_");
}

export function composePhone({ country, digits }: PhoneParts): string {
  if (!digits) return "";
  return country ? `+${country.dial} ${formatNational(country.mask, digits)}` : `+${digits}`;
}

/** Сколько цифр не хватает до полного номера (0 — номер полный). */
export function missingPhoneDigits(value: string): number {
  const { country, digits } = parsePhone(value, null);
  if (country) return phoneSlots(country) - digits.length;
  return Math.max(0, MIN_OTHER_DIGITS - digits.length);
}

export const isPhoneComplete = (value: string) => Boolean(onlyDigits(value)) && missingPhoneDigits(value) === 0;
