import axios, { AxiosError, CanceledError, type InternalAxiosRequestConfig } from "axios";
import { forgetStoredChoice, readStoredChoice, storeChoice } from "@/lib/stored-choice";
import { showToast } from "@/lib/toast";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export const api = axios.create({ baseURL: BASE_URL, timeout: 60_000 });

declare module "axios" {
  interface AxiosRequestConfig {
    /** Вход, регистрация, обновление и выход: без Bearer и без повтора через refresh. */
    skipAuth?: boolean;
  }
}

/*
 * Сессия. Refresh-токен живёт только в HttpOnly-cookie (JS его не видит и не
 * хранит), access — только в памяти вкладки. В localStorage лежит несекретная
 * подсказка {sid, uid}: «в этом браузере есть вход» — для перезагрузки и для
 * соседних вкладок. Пишется она только при входе, стирается при выходе, поэтому
 * ротация refresh не будит другие вкладки.
 */
const SESSION_HINT_KEY = "asyl_session";
// Токены старой схемы (до HttpOnly-cookie): один раз стираем, вход — заново.
const LEGACY_TOKEN_KEYS = ["asyl_access", "asyl_refresh"];
// Одна блокировка на браузер: refresh, вход и выход не пересекаются между вкладками,
// и каждая вкладка отправляет уже повёрнутую cookie, а не ту, что сервер только что отозвал.
const AUTH_LOCK = "asyl-auth-refresh";
const REFRESH_MARGIN_MS = 60_000;
const REFRESH_TIMEOUT_MS = 15_000;
const LOGOUT_TIMEOUT_MS = 5_000;
// Вердикт сервера «этой сессии больше нет». 403 bad_origin, 429, 5xx и сеть —
// не вердикт: кассир не должен вылетать из смены из-за перезагрузки сервера.
const SESSION_ENDING_CODES = new Set([
  "no_active_account",
  "no_session",
  "not_authenticated",
  "password_change_required",
  "password_changed",
  "token_not_valid",
]);

type SessionHint = { sid: string; uid: string };
type AccessClaims = SessionHint & { expiresAt: number };
type AuthRequest = InternalAxiosRequestConfig & {
  _authEpoch?: number;
  _retry?: boolean;
  _sentAccess?: string | null;
};
type RefreshFlight = { epoch: number; sid: string; promise: Promise<string> };

let authEpoch = 0;
let accessToken: string | null = null;
let accessClaims: AccessClaims | null = null;
let refreshing: RefreshFlight | null = null;
const sessionChangeListeners = new Set<() => void>();
let sessionChangeNotifiedEpoch = -1;

if (typeof window !== "undefined") {
  for (const key of LEGACY_TOKEN_KEYS) forgetStoredChoice(key);
}

function parseSessionHint(raw: string | null): SessionHint | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return null;
    const { sid, uid } = value as Record<string, unknown>;
    return typeof sid === "string" && sid && typeof uid === "string" && uid ? { sid, uid } : null;
  } catch {
    return null;
  }
}

function readSessionHint(): SessionHint | null {
  return parseSessionHint(readStoredChoice(SESSION_HINT_KEY));
}

/** Стереть подсказку, только пока она про эту сессию: поздний ответ старой сессии не выкидывает новый вход. */
function forgetSessionHint(sid: string) {
  if (readSessionHint()?.sid === sid) forgetStoredChoice(SESSION_HINT_KEY);
}

/** Claims access-токена — только подсказка: подпись проверяет сервер. */
function decodeAccess(token: string): AccessClaims | null {
  try {
    const payload = token.split(".")[1] ?? "";
    const base64 = payload
      .replace(/-/g, "+")
      .replace(/_/g, "/")
      .padEnd(Math.ceil(payload.length / 4) * 4, "=");
    const claims: unknown = JSON.parse(atob(base64));
    if (!claims || typeof claims !== "object") return null;
    const { sid, user_id: uid, exp, iat } = claims as Record<string, unknown>;
    if (typeof sid !== "string" || !sid || typeof exp !== "number") return null;
    if ((typeof uid !== "string" && typeof uid !== "number") || uid === "") return null;
    // Срок меряем по своим часам от получения: часы кассы могут отставать от сервера.
    const lifetimeMs = typeof iat === "number" ? (exp - iat) * 1000 : exp * 1000 - Date.now();
    return { sid, uid: String(uid), expiresAt: Date.now() + lifetimeMs };
  } catch {
    return null;
  }
}

function sameSession(a: SessionHint, b: SessionHint) {
  return a.sid === b.sid && a.uid === b.uid;
}

function withAuthLock<T>(task: () => Promise<T>): Promise<T> {
  // Без Web Locks (старый Safari, WebView, не-https) страхует окно отсрочки на сервере.
  const locks = typeof navigator === "undefined" ? undefined : navigator.locks;
  return locks ? locks.request(AUTH_LOCK, task) : task();
}

/** Cookie на чужой origin (dev без прокси) уходит и ставится только с withCredentials. */
function isCrossOriginApi(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return new URL(BASE_URL, window.location.href).origin !== window.location.origin;
  } catch {
    return false;
  }
}

function notifySessionChange() {
  // Один сигнал на поколение: синхронизация сама начинает новое.
  if (sessionChangeNotifiedEpoch === authEpoch) return;
  sessionChangeNotifiedEpoch = authEpoch;
  for (const listener of sessionChangeListeners) queueMicrotask(listener);
}

export function invalidateAuthSessionRequests() {
  authEpoch += 1;
  accessToken = null;
  accessClaims = null;
}

/** Новый вход в этой вкладке: access — в память, подсказка — соседним вкладкам. */
export function startSession(access: string) {
  const claims = decodeAccess(access);
  if (!claims) throw new AxiosError("Invalid token response", "ERR_BAD_RESPONSE");
  // Новое поколение: поздний refresh прежней сессии не перезапишет этот вход.
  invalidateAuthSessionRequests();
  accessToken = access;
  accessClaims = claims;
  storeChoice(SESSION_HINT_KEY, JSON.stringify({ sid: claims.sid, uid: claims.uid }));
}

/** Локальный выход вкладки. Подсказку стираем, только если она всё ещё про сессию этой вкладки. */
export function endSession() {
  const sid = accessClaims?.sid;
  invalidateAuthSessionRequests();
  if (sid) forgetSessionHint(sid);
}

export function hasSession(): boolean {
  return readSessionHint() !== null;
}

/** Что storage-событие другой вкладки значит для этой: выход, другой вход или ничего. */
export function sessionHintChange(
  event: Pick<StorageEvent, "key" | "oldValue" | "newValue">,
): "removed" | "replaced" | null {
  // key === null — localStorage.clear() в другой вкладке.
  if (event.key !== null && event.key !== SESSION_HINT_KEY) return null;
  const next = event.key === null ? readSessionHint() : parseSessionHint(event.newValue);
  if (!next) return "removed";
  return parseSessionHint(event.oldValue)?.sid === next.sid ? null : "replaced";
}

/** Cookie браузера оказалась от другого входа — вкладке пора перечитать сессию. */
export function onSessionChange(listener: () => void): () => void {
  sessionChangeListeners.add(listener);
  return () => sessionChangeListeners.delete(listener);
}

/** Вход, первый пароль, регистрация: сервер ставит refresh-cookie и отдаёт access. */
export function requestSession(url: string, body: object, signal?: AbortSignal): Promise<string> {
  return withAuthLock(() => api.post<{ access: string }>(url, body, { signal, skipAuth: true })).then(
    ({ data }) => data.access,
  );
}

/** Выход на сервере: отзыв refresh-cookie и cookie камер. Не бросает — локальный выход будет всё равно. */
export async function revokeServerSession(): Promise<void> {
  try {
    await withAuthLock(() => api.post("/auth/logout/", {}, { skipAuth: true, timeout: LOGOUT_TIMEOUT_MS }));
  } catch {
    // Нет связи или сервер лежит: выходим локально, cookie не отозвана.
  }
}

export function staleAuthSession(): CanceledError<unknown> {
  return new CanceledError("Authentication session changed");
}

function endsSession(error: unknown): boolean {
  return (error as AxiosError | undefined)?.response?.status === 401 && SESSION_ENDING_CODES.has(apiErrorCode(error));
}

/** Сервер сказал, что сессии нет: забыть её во всех вкладках и открыть вход. */
function endDeadSession(epoch: number, sid: string) {
  forgetSessionHint(sid);
  if (epoch !== authEpoch) return;
  invalidateAuthSessionRequests();
  if (typeof window !== "undefined" && window.location.pathname !== "/login") window.location.href = "/login";
}

async function exchangeRefresh(epoch: number, session: SessionHint, staleAccess: string | null): Promise<string> {
  if (epoch !== authEpoch) throw staleAuthSession();
  // Пока ждали блокировку, этот же сеанс вкладки мог уже получить свежий access.
  if (
    accessToken &&
    accessToken !== staleAccess &&
    accessClaims &&
    sameSession(accessClaims, session) &&
    accessClaims.expiresAt - Date.now() > REFRESH_MARGIN_MS
  ) {
    return accessToken;
  }
  // Не обрываем: сервер мог уже повернуть cookie. Устаревший ответ просто отбрасываем.
  const response = await api
    .post<{ access?: unknown }>("/auth/refresh/", {}, { skipAuth: true, timeout: REFRESH_TIMEOUT_MS })
    .catch((error: unknown) => {
      if (endsSession(error)) endDeadSession(epoch, session.sid);
      throw error;
    });
  const access = response.data?.access;
  const claims = typeof access === "string" ? decodeAccess(access) : null;
  if (typeof access !== "string" || !claims) {
    throw new AxiosError("Invalid token refresh response", "ERR_BAD_RESPONSE");
  }
  if (epoch !== authEpoch) throw staleAuthSession();
  const hint = readSessionHint();
  if (!sameSession(claims, session)) {
    // Cookie браузера — от другого входа. Если об этом входе никто не объявил,
    // сессия вкладки здесь закончилась; иначе вкладка переходит на новый вход.
    if (hint?.sid === session.sid) endDeadSession(epoch, session.sid);
    else notifySessionChange();
    throw staleAuthSession();
  }
  if (hint?.sid !== session.sid) {
    notifySessionChange();
    throw staleAuthSession();
  }
  accessToken = access;
  accessClaims = claims;
  return access;
}

/** Один refresh на вкладку для поколения и сессии; между вкладками — через Web Locks. */
function refreshAccess(epoch: number, session: SessionHint, staleAccess: string | null): Promise<string> {
  if (refreshing?.epoch === epoch && refreshing.sid === session.sid) return refreshing.promise;
  const promise = withAuthLock(() => exchangeRefresh(epoch, session, staleAccess)).finally(() => {
    if (refreshing?.promise === promise) refreshing = null;
  });
  refreshing = { epoch, sid: session.sid, promise };
  return promise;
}

/** Access для запроса: после перезагрузки — сначала refresh по cookie, перед истечением — заранее. */
async function accessFor(epoch: number): Promise<string | null> {
  const session = readSessionHint();
  if (!session) return null;
  const current = accessToken;
  if (current && accessClaims) {
    if (!sameSession(accessClaims, session)) {
      // Другая вкладка вошла под другим пользователем: не шлём запрос ни от старого, ни от нового.
      notifySessionChange();
      throw staleAuthSession();
    }
    if (accessClaims.expiresAt - Date.now() > REFRESH_MARGIN_MS) return current;
  }
  try {
    return await refreshAccess(epoch, session, current);
  } catch (error) {
    // Заранее обновить не вышло (сеть, 5xx), а текущий access ещё действует — работаем с ним.
    if (
      current &&
      current === accessToken &&
      accessClaims &&
      accessClaims.expiresAt > Date.now() &&
      !isCanceledRequest(error)
    ) {
      return current;
    }
    throw error;
  }
}

api.interceptors.request.use(async (config) => {
  const request = config as AuthRequest;
  if (request._authEpoch === undefined) request._authEpoch = authEpoch;
  else if (request._authEpoch !== authEpoch) throw staleAuthSession();
  if (request.skipAuth) {
    if (isCrossOriginApi()) config.withCredentials = true;
    return config;
  }
  const token = await accessFor(request._authEpoch);
  if (request._authEpoch !== authEpoch) throw staleAuthSession();
  request._sentAccess = token;
  if (token) config.headers.set("Authorization", `Bearer ${token}`);
  else config.headers.delete("Authorization");
  return config;
});

/** Повтор запроса после 401: один refresh на всех, затем тот же запрос с новым access. */
async function retryWithFreshAccess(original: AuthRequest, session: SessionHint) {
  original._retry = true;
  const epoch = authEpoch;
  if (accessClaims && !sameSession(accessClaims, session)) {
    notifySessionChange();
    throw staleAuthSession();
  }
  // Сессию завершает только вердикт сервера — это уже сделал refresh. Сеть, 5xx, 429
  // и 403 уходят вызывающему как есть: он видит настоящую причину, а сессия цела.
  await refreshAccess(epoch, session, original._sentAccess ?? null);
  if (epoch !== authEpoch) throw staleAuthSession();
  return api(original);
}

api.interceptors.response.use(
  (response) => {
    const request = response.config as AuthRequest;
    if (request._authEpoch !== authEpoch) throw staleAuthSession();
    return response;
  },
  async (error: AxiosError) => {
    const original = error.config as AuthRequest | undefined;
    if (original?._authEpoch !== undefined && original._authEpoch !== authEpoch) {
      return Promise.reject(staleAuthSession());
    }
    const session =
      error.response?.status === 401 && original && !original.skipAuth && !original._retry ? readSessionHint() : null;
    if (original && session) return retryWithFreshAccess(original, session);
    if (error.response?.status === 403) showToast(errorDetail(error));
    return Promise.reject(error);
  },
);

function errorDetail(e: unknown): string {
  const err = e as AxiosError<{ detail?: string; code?: string }>;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return Object.values(detail).flat().join("; ");

  if (err.code === "ECONNABORTED" || err.code === "ETIMEDOUT") {
    return "Сервер не подтвердил результат вовремя. Обновите данные перед повтором операции.";
  }
  if (err.response?.status === 400) {
    const body: unknown = err.response.data;
    if (body && typeof body === "object") {
      const messages = Object.entries(body)
        .filter(([field]) => field !== "code")
        .flatMap(([, value]: [string, unknown]) => (Array.isArray(value) ? value : [value]))
        .filter((value): value is string => typeof value === "string");
      if (messages.length) return messages.join("; ");
    }
  }

  // Без ответа сервера причина другая, и действие пользователя другое:
  // «нет связи» лечится проверкой сети, а не повторным нажатием кнопки.
  if (!err.response) return "Нет связи с сервером. Проверьте интернет и повторите.";
  const status = err.response.status;
  if (status >= 500) return "Сервер не отвечает. Попробуйте через минуту или сообщите администратору.";
  if (status === 404) return "Запись не найдена — возможно, её уже удалили.";
  if (status === 401) return "Сессия истекла. Войдите заново.";
  return "Произошла ошибка. Попробуйте ещё раз.";
}

export function apiError(e: unknown): string {
  // 403 уже показан всплывающим алертом (интерцептор выше) — на странице не дублируем.
  if ((e as AxiosError).response?.status === 403) return "";
  return errorDetail(e);
}

/** apiError для запросов с responseType:"blob": тело ошибки приходит Blob-ом,
 * и без распаковки текст «detail» от сервера терялся в общей формулировке. */
export async function blobApiError(e: unknown): Promise<string> {
  const response = (e as AxiosError).response;
  if (response && response.data instanceof Blob) {
    try {
      const parsed = JSON.parse(await response.data.text());
      return apiError({ ...(e as object), response: { ...response, data: parsed } });
    } catch {
      // Не JSON — падаем в обычную обработку по статусу.
    }
  }
  return apiError(e);
}

/** Машинный `code` из тела ошибки API; "" — если сервер его не прислал. */
export function apiErrorCode(e: unknown): string {
  const code = (e as AxiosError<{ code?: unknown }> | undefined)?.response?.data?.code;
  return typeof code === "string" ? code : "";
}

export function isCanceledRequest(error: unknown): boolean {
  return axios.isCancel(error) || (error as AxiosError | undefined)?.code === "ERR_CANCELED";
}
