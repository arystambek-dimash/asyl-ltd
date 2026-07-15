import axios, { AxiosError } from "axios";
import { showToast } from "@/lib/toast";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export const api = axios.create({ baseURL: BASE_URL });

const ACCESS = "asyl_access";
const REFRESH = "asyl_refresh";

export function setTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS, access);
  localStorage.setItem(REFRESH, refresh);
}
export function clearTokens() {
  localStorage.removeItem(ACCESS);
  localStorage.removeItem(REFRESH);
}
export function getAccess() {
  return typeof window !== "undefined" ? localStorage.getItem(ACCESS) : null;
}

api.interceptors.request.use((config) => {
  const token = getAccess();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

let refreshing: Promise<string> | null = null;

api.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config!;
    const refresh =
      typeof window !== "undefined" ? localStorage.getItem(REFRESH) : null;
    if (
      error.response?.status === 401 &&
      refresh &&
      !(original as { _retry?: boolean })._retry
    ) {
      (original as { _retry?: boolean })._retry = true;
      try {
        if (!refreshing) {
          refreshing = axios
            .post(`${BASE_URL}/auth/refresh/`, { refresh })
            .then((res) => {
              const access = res.data.access as string;
              localStorage.setItem(ACCESS, access);
              return access;
            })
            .finally(() => {
              refreshing = null;
            });
        }
        const access = await refreshing;
        original.headers!.Authorization = `Bearer ${access}`;
        return api(original);
      } catch {
        clearTokens();
        if (typeof window !== "undefined") window.location.href = "/login";
      }
    }
    if (error.response?.status === 403) showToast(errorDetail(error));
    return Promise.reject(error);
  }
);

function errorDetail(e: unknown): string {
  const err = e as AxiosError<{ detail?: string; code?: string }>;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return Object.values(detail).flat().join("; ");
  return "Произошла ошибка. Попробуйте ещё раз.";
}

export function apiError(e: unknown): string {
  // 403 уже показан всплывающим алертом (интерцептор выше) — на странице не дублируем.
  if ((e as AxiosError).response?.status === 403) return "";
  return errorDetail(e);
}
