/**
 * Выбор на экране, который помнит браузер: отдел кассы, вкладка грузчика.
 * Ключ — на пользователя: на общем телефоне или планшете сменщик не
 * наследует чужой выбор. Приватный режим или запрет хранилища — не ошибка:
 * выбор живёт до перезагрузки.
 */
export function userChoiceKey(base: string, userId?: number): string {
  return userId ? `${base}:${userId}` : base;
}

export function readStoredChoice(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storeChoice(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Запрет хранилища — выбор живёт до перезагрузки.
  }
}
