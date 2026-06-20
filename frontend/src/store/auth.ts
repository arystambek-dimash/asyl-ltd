import { create } from "zustand";
import { api, setTokens, clearTokens } from "@/lib/api";
import type { Me } from "@/lib/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  loadMe: () => Promise<void>;
  login: (username: string, password: string) => Promise<Me>;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  me: null,
  loading: true,
  loadMe: async () => {
    try {
      const { data } = await api.get<Me>("/auth/me/");
      set({ me: data, loading: false });
    } catch {
      set({ me: null, loading: false });
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
