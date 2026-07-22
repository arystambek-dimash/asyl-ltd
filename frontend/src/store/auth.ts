import { create } from "zustand";
import { CanceledError } from "axios";
import { api, setTokens, clearTokens, hasAuthTokens, invalidateAuthSessionRequests } from "@/lib/api";
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
  adoptSession: (access: string, refresh: string) => Promise<Me>;
  /** Re-read the session after another tab replaces the shared credentials. */
  syncExternalSession: () => Promise<void>;
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

function staleAuthOperation() {
  return new CanceledError("Authentication session changed");
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

type AuthCommit = (state: { me: Me | null; loading: boolean }) => void;

function beginSession(commit: AuthCommit) {
  const generation = nextAuthGeneration();
  invalidateAuthSessionRequests();
  invalidateCameraStreamToken();
  lastMeFetch = 0;
  commit({ me: null, loading: true });
  return generation;
}

async function commitSessionMe(generation: number, commit: AuthCommit): Promise<Me> {
  const me = await requestMe(generation);
  if (generation !== authGeneration) throw staleAuthOperation();
  lastMeFetch = Date.now();
  commit({ me, loading: false });
  return me;
}

export const useAuth = create<AuthState>((set, get) => ({
  me: null,
  loading: true,
  loadMe: async () => {
    if (!hasAuthTokens()) {
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
    } catch {
      if (generation !== authGeneration) return;
      set({ me: null, loading: false });
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
    } catch {
      /* сеть моргнула — оставляем текущие права */
    }
  },
  login: async (username, password) => {
    const generation = beginSession(set);
    const controller = new AbortController();
    loginController = controller;
    try {
      const { data } = await api.post<{ access: string; refresh: string }>(
        "/auth/login/",
        { username, password },
        { signal: controller.signal },
      );
      if (generation !== authGeneration) throw staleAuthOperation();
      setTokens(data.access, data.refresh);
      return await commitSessionMe(generation, set);
    } catch (error) {
      if (generation === authGeneration) set({ loading: false });
      throw error;
    } finally {
      if (loginController === controller) loginController = null;
    }
  },
  adoptSession: async (access, refresh) => {
    const generation = beginSession(set);
    try {
      setTokens(access, refresh);
      return await commitSessionMe(generation, set);
    } catch (error) {
      if (generation === authGeneration) set({ loading: false });
      throw error;
    }
  },
  syncExternalSession: async () => {
    const generation = beginSession(set);
    if (!hasAuthTokens()) {
      if (generation === authGeneration) set({ me: null, loading: false });
      return;
    }
    try {
      await commitSessionMe(generation, set);
    } catch {
      if (generation === authGeneration) set({ me: null, loading: false });
    }
  },
  logout: () => {
    nextAuthGeneration();
    invalidateCameraStreamToken();
    clearTokens();
    lastMeFetch = 0;
    set({ me: null, loading: false });
  },
}));
