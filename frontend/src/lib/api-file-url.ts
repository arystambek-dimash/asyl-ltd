import { api } from "@/lib/api";

/**
 * Абсолютный адрес приватного файла бэкенда по относительной ссылке вида
 * `/api/grain/photos/...`. API-клиент знает свой origin, `<img>` — нет.
 */
export function apiFileUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  if (/^https?:\/\//.test(path)) return path;
  const base = (api.defaults?.baseURL ?? "").replace(/\/api\/?$/, "");
  return `${base}${path}`;
}
