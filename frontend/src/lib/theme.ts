import { readStoredChoice, storeChoice } from "@/lib/stored-choice";

/**
 * Тема оформления — настройка браузера, а не экрана: применяется до
 * гидратации inline-скриптом корневого layout (вход, «Загрузка…» и
 * перезагрузка без светлой вспышки), а меню профиля только меняет выбор.
 */
export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "asyl_theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

export function readTheme(): Theme {
  const stored = readStoredChoice(THEME_KEY);
  return stored === "dark" || stored === "system" ? stored : "light";
}

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia(DARK_QUERY).matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Сохраняет выбор и сразу применяет: без хранилища тема живёт до перезагрузки. */
export function pickTheme(theme: Theme) {
  storeChoice(THEME_KEY, theme);
  applyTheme(theme);
}

/**
 * Уходит в страницу строкой и выполняется до React, поэтому самодостаточна:
 * без импортов и замыканий. Смену системной темы слушает сама — выбор «Авто»
 * работает на любой странице, а не только при открытой шапке.
 */
function initTheme(key: string, query: string) {
  const media = window.matchMedia(query);
  const sync = () => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(key);
    } catch {
      // Приватный режим без localStorage — остаёмся на светлой.
    }
    document.documentElement.classList.toggle("dark", stored === "dark" || (stored === "system" && media.matches));
  };
  sync();
  media.addEventListener("change", sync);
}

export const THEME_INIT_SCRIPT = `(${initTheme.toString()})(${JSON.stringify(THEME_KEY)}, ${JSON.stringify(DARK_QUERY)});`;
