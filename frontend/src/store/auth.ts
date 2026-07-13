import { create } from "zustand";
import { api, setTokens, clearTokens } from "@/lib/api";
import type { Me } from "@/lib/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  loadMe: () => Promise<void>;
  /** Тихо перечитать права — без скачка «Загрузка…».
   * Без force срабатывает не чаще раза в минуту (для фокуса вкладки). */
  refreshMe: (force?: boolean) => Promise<void>;
  login: (username: string, password: string) => Promise<Me>;
  logout: () => void;
}

let lastMeFetch = 0;

export const useAuth = create<AuthState>((set, get) => ({
  me: null,
  loading: true,
  loadMe: async () => {
    try {
      const { data } = await api.get<Me>("/auth/me/");
      lastMeFetch = Date.now();
      set({ me: data, loading: false });
    } catch {
      set({ me: null, loading: false });
    }
  },
  refreshMe: async (force = false) => {
    if (!get().me || (!force && Date.now() - lastMeFetch < 60_000)) return;
    lastMeFetch = Date.now();
    try {
      const { data } = await api.get<Me>("/auth/me/");
      set({ me: data });
    } catch {
      /* сеть моргнула — оставляем текущие права */
    }
  },
  login: async (username, password) => {
    const { data } = await api.post("/auth/login/", { username, password });
    setTokens(data.access, data.refresh);
    const me = await api.get<Me>("/auth/me/");
    set({ me: me.data, loading: false });
    return me.data;
  },
  logout: () => {
    clearTokens();
    set({ me: null });
  },
}));
