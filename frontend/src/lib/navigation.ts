const LOCAL_ORIGIN = "http://asyl.local";

/** Обратный путь для страниц-«листов»: только внутренний путь приложения.
 * Сравниваем origin после разбора по правилам браузера — строковая проверка
 * пропускала `/\evil.com` и `/\t/evil.com`, которые браузер читает как чужой хост. */
export function safeBackPath(raw: string | null | undefined): string | null {
  if (!raw || !raw.startsWith("/")) return null;
  let url: URL;
  try {
    url = new URL(raw, LOCAL_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== LOCAL_ORIGIN) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Ссылка на карточку с запомненным обратным путём: /orders/12?back=%2Faccounting%3Fview%3Dconfirm */
export function withBack(href: string, back: string): string {
  return `${href}${href.includes("?") ? "&" : "?"}back=${encodeURIComponent(back)}`;
}
