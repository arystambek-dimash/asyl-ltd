import { create } from "zustand";
import {
  api,
  endSession,
  hasSession,
  invalidateAuthSessionRequests,
  requestSession,
  revokeServerSession,
  staleAuthSession,
  startSession,
} from "@/lib/api";
import { invalidateCameraStreamToken } from "@/lib/camera-stream-auth";
import type { Me } from "@/lib/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  loadMe: () => Promise<void>;
  /** Тихо перечитать права — без скачка «Загрузка…».
   * Без force срабатывает не чаще раза в минуту (для фокуса вкладки). */
  refreshMe: (force?: boolean) => Promise<void>;
  login: (username: string, password: string) => Promise<Me>;
  completeInitialPasswordChange: (username: string, currentPassword: string, newPassword: string) => Promise<Me>;
  /** Сессия, которую уже открыл сервер (регистрация клиента): access из ответа. */
  adoptSession: (access: string) => Promise<Me>;
  /** Перечитать сессию, когда другая вкладка вошла под другим пользователем. */
  syncExternalSession: () => Promise<void>;
  /** «Выйти»: отозвать сессию на сервере (refresh- и camera-cookie), затем выйти локально. */
  signOut: () => Promise<void>;
  /** Локальный выход вкладки: 401, выход в другой вкладке, неудачный вход. */
  logout: () => void;
}

let lastMeFetch = 0;
let authGeneration = 0;
let loginController: AbortController | null = null;

type MeFlight = {
  controller: AbortController;
  generation: number;
  promise: Promise<Me>;
};

let meRequest: MeFlight | null = null;

function nextAuthGeneration() {
  authGeneration += 1;
  loginController?.abort();
  loginController = null;
  meRequest?.controller.abort();
  meRequest = null;
  return authGeneration;
}

function requestMe(generation: number): Promise<Me> {
  if (meRequest?.generation === generation) return meRequest.promise;
  meRequest?.controller.abort();

  const controller = new AbortController();
  const promise = api
    .get<Me>("/auth/me/", { signal: controller.signal })
    .then(({ data }) => data)
    .finally(() => {
      if (meRequest?.promise === promise) meRequest = null;
    });
  const flight = { controller, generation, promise } satisfies MeFlight;
  meRequest = flight;
  return promise;
}

type AuthCommit = (state: Partial<Pick<AuthState, "me" | "loading">>) => void;

// Выход — только по 401. 403 (bad_origin, нет права) — не вердикт о сессии:
// ошибка настройки Origin не должна выкидывать всех из системы.
function isUnauthorized(error: unknown) {
  return (error as { response?: { status?: number } } | null)?.response?.status === 401;
}

function beginSession(commit: AuthCommit, currentMe: Me | null = null) {
  const generation = nextAuthGeneration();
  invalidateAuthSessionRequests();
  invalidateCameraStreamToken();
  lastMeFetch = 0;
  commit({ me: currentMe, loading: true });
  return generation;
}

async function commitSessionMe(generation: number, commit: AuthCommit): Promise<Me> {
  const me = await requestMe(generation);
  if (generation !== authGeneration) throw staleAuthSession();
  lastMeFetch = Date.now();
  commit({ me, loading: false });
  return me;
}

/** Начать новую сессию: получить access (refresh сервер кладёт в HttpOnly-cookie),
 * запомнить его и загрузить /auth/me/.
 * Смена поколения (выход, другая вкладка) обрывает запрос и отбрасывает результат. */
async function openSession(
  commit: AuthCommit,
  get: () => AuthState,
  obtainAccess: (signal: AbortSignal) => string | Promise<string>,
): Promise<Me> {
  const generation = beginSession(commit);
  const controller = new AbortController();
  loginController = controller;
  try {
    const access = await obtainAccess(controller.signal);
    if (generation !== authGeneration) throw staleAuthSession();
    startSession(access);
    return await commitSessionMe(generation, commit);
  } catch (error) {
    if (generation === authGeneration) {
      if (isUnauthorized(error)) get().logout();
      else commit({ loading: false });
    }
    throw error;
  } finally {
    if (loginController === controller) loginController = null;
  }
}

export const useAuth = create<AuthState>((set, get) => ({
  me: null,
  loading: true,
  loadMe: async () => {
    if (!hasSession()) {
      // Login/register pages mount this eagerly. Do not generate a guaranteed
      // 401 (and a refresh attempt) when the browser has no session at all.
      if (!loginController) nextAuthGeneration();
      invalidateCameraStreamToken();
      set({ me: null, loading: false });
      return;
    }
    if (get().me) {
      set({ loading: false });
      return;
    }
    const generation = authGeneration;
    set({ loading: true });
    try {
      const data = await requestMe(generation);
      if (generation !== authGeneration) return;
      lastMeFetch = Date.now();
      set({ me: data, loading: false });
    } catch (error) {
      if (generation !== authGeneration) return;
      if (isUnauthorized(error)) {
        get().logout();
        return;
      }
      // A saved session is still valid until the server explicitly rejects it.
      // Offline/5xx failures leave the credentials intact so AppShell can
      // retry on focus or when connectivity returns.
      set({ loading: false });
    }
  },
  refreshMe: async (force = false) => {
    if (!get().me || (!force && lastMeFetch > 0 && Date.now() - lastMeFetch < 60_000)) return;
    const generation = authGeneration;
    try {
      const data = await requestMe(generation);
      if (generation !== authGeneration) return;
      lastMeFetch = Date.now();
      set({ me: data });
    } catch (error) {
      if (generation !== authGeneration) return;
      if (isUnauthorized(error)) get().logout();
      // Network/5xx: retain the last known identity and permissions.
    }
  },
  login: (username, password) =>
    openSession(set, get, (signal) => requestSession("/auth/login/", { username, password }, signal)),
  completeInitialPasswordChange: (username, currentPassword, newPassword) =>
    openSession(set, get, (signal) =>
      requestSession(
        "/auth/initial-password/",
        { username, current_password: currentPassword, new_password: newPassword },
        signal,
      ),
    ),
  adoptSession: (access) => openSession(set, get, () => access),
  syncExternalSession: async () => {
    const generation = beginSession(set, get().me);
    if (!hasSession()) {
      if (generation === authGeneration) set({ me: null, loading: false });
      return;
    }
    try {
      await commitSessionMe(generation, set);
    } catch (error) {
      if (generation !== authGeneration) return;
      if (isUnauthorized(error)) {
        get().logout();
        return;
      }
      // Общая cookie уже от другого входа: прежнего пользователя не возвращаем,
      // иначе экран показывал бы одного, а запросы шли бы от другого.
      // AppShell повторит загрузку, пока подсказка о входе на месте.
      set({ me: null, loading: false });
    }
  },
  signOut: async () => {
    // Пока cookie ещё в браузере: сервер отзовёт её и сотрёт cookie камер.
    await revokeServerSession();
    get().logout();
  },
  logout: () => {
    nextAuthGeneration();
    invalidateCameraStreamToken();
    endSession();
    lastMeFetch = 0;
    set({ me: null, loading: false });
  },
}));
