import axios, { AxiosError, CanceledError, type InternalAxiosRequestConfig } from "axios";
import { showToast } from "@/lib/toast";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export const api = axios.create({ baseURL: BASE_URL });

const AUTH_STORAGE_KEYS = {
  access: "asyl_access",
  refresh: "asyl_refresh",
} as const;

export function authTokenStorageKey(token: keyof typeof AUTH_STORAGE_KEYS) {
  return AUTH_STORAGE_KEYS[token];
}

export function isRefreshTokenRemoval(event: Pick<StorageEvent, "key" | "newValue">) {
  return event.key === authTokenStorageKey("refresh") && event.newValue === null;
}

export function isRefreshTokenReplacement(event: Pick<StorageEvent, "key" | "oldValue" | "newValue">) {
  return event.key === authTokenStorageKey("refresh") && event.newValue !== null && event.newValue !== event.oldValue;
}

const ACCESS = authTokenStorageKey("access");
const REFRESH = authTokenStorageKey("refresh");

type RetryableRequest = InternalAxiosRequestConfig & {
  _authEpoch?: number;
  _retry?: boolean;
};
type RefreshFlight = {
  controller: AbortController;
  epoch: number;
  refresh: string;
  promise: Promise<string>;
};

let authEpoch = 0;
let refreshing: RefreshFlight | null = null;

export function invalidateAuthSessionRequests() {
  authEpoch += 1;
  refreshing?.controller.abort();
  refreshing = null;
}

export function setTokens(access: string, refresh: string) {
  // A new login/registration is a new auth generation. A late refresh from the
  // previous session must never overwrite these credentials.
  invalidateAuthSessionRequests();
  localStorage.setItem(ACCESS, access);
  localStorage.setItem(REFRESH, refresh);
}
export function clearTokens() {
  // Abort the HTTP request where possible and, more importantly, invalidate
  // its result for adapters/environments that cannot be aborted reliably.
  invalidateAuthSessionRequests();
  localStorage.removeItem(ACCESS);
  localStorage.removeItem(REFRESH);
}
export function getAccess() {
  return typeof window !== "undefined" ? localStorage.getItem(ACCESS) : null;
}

function getRefresh() {
  return typeof window !== "undefined" ? localStorage.getItem(REFRESH) : null;
}

export function hasAuthTokens() {
  return Boolean(getAccess() || getRefresh());
}

api.interceptors.request.use((config) => {
  const request = config as RetryableRequest;
  if (request._authEpoch === undefined) request._authEpoch = authEpoch;
  else if (request._authEpoch !== authEpoch) throw staleAuthRequest();
  const token = getAccess();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

function staleAuthRequest(): CanceledError<unknown> {
  return new CanceledError("Authentication session changed");
}

function refreshAccess(refresh: string, epoch: number): Promise<string> {
  if (refreshing && (refreshing.epoch !== epoch || refreshing.refresh !== refresh)) {
    refreshing.controller.abort();
    refreshing = null;
  }
  if (refreshing) return refreshing.promise;

  const controller = new AbortController();
  const promise = axios
    .post<{ access?: unknown }>(`${BASE_URL}/auth/refresh/`, { refresh }, { signal: controller.signal })
    .then((res) => {
      if (epoch !== authEpoch || getRefresh() !== refresh) throw staleAuthRequest();
      const access = res.data.access;
      if (typeof access !== "string" || !access) {
        throw new AxiosError("Invalid token refresh response", "ERR_BAD_RESPONSE");
      }
      localStorage.setItem(ACCESS, access);
      return access;
    })
    .finally(() => {
      if (refreshing?.promise === promise) refreshing = null;
    });
  const flight = { controller, epoch, refresh, promise } satisfies RefreshFlight;
  refreshing = flight;
  return promise;
}

api.interceptors.response.use(
  (response) => {
    const request = response.config as RetryableRequest;
    if (request._authEpoch !== authEpoch) throw staleAuthRequest();
    return response;
  },
  async (error: AxiosError) => {
    const original = error.config as RetryableRequest | undefined;
    if (original?._authEpoch !== undefined && original._authEpoch !== authEpoch) {
      return Promise.reject(staleAuthRequest());
    }
    const refresh = getRefresh();
    if (error.response?.status === 401 && original && refresh && !original._retry) {
      original._retry = true;
      const epoch = authEpoch;
      try {
        const access = await refreshAccess(refresh, epoch);
        if (epoch !== authEpoch || getRefresh() !== refresh) throw staleAuthRequest();
        original._authEpoch = epoch;
        original.headers!.Authorization = `Bearer ${access}`;
        return api(original);
      } catch (refreshError) {
        if (isCanceledRequest(refreshError)) return Promise.reject(refreshError);
        // Разлогиниваем только по вердикту сервера о refresh-токене. Сетевой
        // сбой (сервер перезагружается) не должен выбрасывать кассира из смены.
        const status = (refreshError as AxiosError).response?.status;
        if (status === 401 || status === 403) {
          clearTokens();
          if (typeof window !== "undefined") window.location.href = "/login";
        }
      }
    }
    if (error.response?.status === 403) showToast(errorDetail(error));
    return Promise.reject(error);
  },
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

export function isCanceledRequest(error: unknown): boolean {
  return axios.isCancel(error) || (error as AxiosError | undefined)?.code === "ERR_CANCELED";
}
