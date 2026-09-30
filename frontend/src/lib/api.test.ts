import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  apiError,
  endSession,
  hasSession,
  invalidateAuthSessionRequests,
  onSessionChange,
  requestSession,
  revokeServerSession,
  sessionHintChange,
  startSession,
} from "@/lib/api";

const HINT_KEY = "asyl_session";

function base64Url(value: object) {
  return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

let jti = 0;
/** Access-токен как у simplejwt: user_id строкой, sid копируется при ротации. */
function jwt(sid: string, uid = "7", lifetimeSeconds = 900) {
  const iat = Math.floor(Date.now() / 1000);
  jti += 1;
  return `header.${base64Url({ token_type: "access", user_id: uid, sid, iat, exp: iat + lifetimeSeconds, jti })}.signature`;
}

function hint(sid: string, uid = "7") {
  return JSON.stringify({ sid, uid });
}

function ok(config: InternalAxiosRequestConfig, data: unknown = { ok: true }): AxiosResponse {
  return { config, data, headers: {}, status: 200, statusText: "OK" };
}

function failure(config: InternalAxiosRequestConfig, status: number, data: unknown = {}): never {
  const response = { config, data, headers: {}, status, statusText: String(status) } as AxiosResponse;
  throw new AxiosError(String(status), "ERR_BAD_REQUEST", config, undefined, response);
}

type Handler = (config: InternalAxiosRequestConfig) => Promise<AxiosResponse> | AxiosResponse;

/** Один адаптер на всё: /auth/refresh/ отдельно, остальные запросы — `other`. */
function serve({ refresh, other }: { refresh?: Handler; other?: Handler }) {
  const refreshCalls: InternalAxiosRequestConfig[] = [];
  const otherCalls: InternalAxiosRequestConfig[] = [];
  const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
    if (config.url === "/auth/refresh/") {
      refreshCalls.push(config);
      if (!refresh) throw new Error("unexpected refresh");
      return refresh(config);
    }
    otherCalls.push(config);
    return other ? other(config) : ok(config);
  });
  api.defaults.adapter = adapter;
  return { adapter, refreshCalls, otherCalls };
}

function bearer(config: InternalAxiosRequestConfig) {
  return config.headers.Authorization;
}

function storedKeys() {
  return Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index));
}

function setLocks(request: (name: string, task: () => Promise<unknown>) => Promise<unknown>) {
  Object.defineProperty(navigator, "locks", { configurable: true, value: { request: vi.fn(request) } });
  return (navigator as unknown as { locks: { request: ReturnType<typeof vi.fn> } }).locks.request;
}

const originalAdapter = api.defaults.adapter;

beforeEach(() => {
  localStorage.clear();
  invalidateAuthSessionRequests();
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
  invalidateAuthSessionRequests();
  localStorage.clear();
  Reflect.deleteProperty(navigator, "locks");
  window.history.replaceState(null, "", "/");
});

describe("api errors", () => {
  it("bounds requests and explains an unknown outcome after a timeout", () => {
    expect(api.defaults.timeout).toBe(60_000);
    expect(apiError(new AxiosError("timeout", "ECONNABORTED"))).toContain("Обновите данные перед повтором");
  });

  it("preserves field validation messages from the backend", () => {
    expect(apiError({ response: { status: 400, data: { assignee: ["Выберите действующего сотрудника"] } } })).toBe(
      "Выберите действующего сотрудника",
    );
  });
});

describe("session storage", () => {
  it("keeps tokens in memory and only a non-secret hint in localStorage", async () => {
    const access = jwt("s1");
    startSession(access);

    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s1"));
    expect(storedKeys()).toEqual([HINT_KEY]);
    expect(hasSession()).toBe(true);

    const { otherCalls } = serve({});
    await api.get("/protected/");
    expect(bearer(otherCalls[0])).toBe(`Bearer ${access}`);
  });

  it("rejects a token response without a session id", () => {
    expect(() => startSession("not-a-jwt")).toThrow("Invalid token response");
    expect(hasSession()).toBe(false);
  });

  it("removes legacy localStorage tokens once at startup", async () => {
    localStorage.setItem("asyl_access", "old-access");
    localStorage.setItem("asyl_refresh", "old-refresh");
    localStorage.setItem("asyl_theme", "dark");

    vi.resetModules();
    await import("@/lib/api");

    expect(localStorage.getItem("asyl_access")).toBeNull();
    expect(localStorage.getItem("asyl_refresh")).toBeNull();
    expect(localStorage.getItem("asyl_theme")).toBe("dark");
  });

  it("ends only the session this tab belongs to", () => {
    startSession(jwt("s1"));
    localStorage.setItem(HINT_KEY, hint("s2"));

    endSession();

    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s2"));
  });

  it.each([
    ["removal", { key: HINT_KEY, oldValue: hint("s1"), newValue: null }, "removed"],
    ["another login", { key: HINT_KEY, oldValue: hint("s1"), newValue: hint("s2") }, "replaced"],
    ["first login", { key: HINT_KEY, oldValue: null, newValue: hint("s1") }, "replaced"],
    ["same session", { key: HINT_KEY, oldValue: hint("s1"), newValue: hint("s1") }, null],
    ["corrupted hint", { key: HINT_KEY, oldValue: hint("s1"), newValue: "{" }, "removed"],
    ["legacy token key", { key: "asyl_refresh", oldValue: "a", newValue: null }, null],
    ["cleared storage", { key: null, oldValue: null, newValue: null }, "removed"],
  ] as const)("classifies a storage event: %s", (_label, event, expected) => {
    expect(sessionHintChange(event)).toBe(expected);
  });
});

describe("cookie refresh", () => {
  it("bootstraps once after a reload and shares the refresh between parallel requests", async () => {
    localStorage.setItem(HINT_KEY, hint("s1"));
    const fresh = jwt("s1");
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return ok(config, { access: fresh });
      },
    });

    const requests = Promise.all([api.get("/a/"), api.get("/b/"), api.post("/c/", { x: 1 })]);
    await vi.waitFor(() => expect(refreshCalls).toHaveLength(1));
    expect(otherCalls).toHaveLength(0);
    refresh.resolve();
    await requests;

    const [call] = refreshCalls;
    expect(call.method).toBe("post");
    expect(call.data).toBe("{}");
    expect(bearer(call)).toBeUndefined();
    expect(call.signal).toBeUndefined();
    // Тестовый origin (localhost:3000) ≠ API (localhost:8000): cookie только с credentials.
    expect(call.withCredentials).toBe(true);
    expect(otherCalls.map(bearer)).toEqual([`Bearer ${fresh}`, `Bearer ${fresh}`, `Bearer ${fresh}`]);
    expect(otherCalls.every((config) => !config.withCredentials)).toBe(true);
  });

  it("sends no token and never refreshes without a session", async () => {
    const { otherCalls } = serve({ other: (config) => failure(config, 401) });

    await expect(api.get("/protected/")).rejects.toMatchObject({ response: { status: 401 } });

    expect(bearer(otherCalls[0])).toBeUndefined();
  });

  it("refreshes an expired access once and retries with the new one", async () => {
    const stale = jwt("s1");
    const fresh = jwt("s1");
    startSession(stale);
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return ok(config, { access: fresh });
      },
      other: (config) => (bearer(config) === `Bearer ${stale}` ? failure(config, 401) : ok(config)),
    });

    const requests = Promise.all([api.get("/first/"), api.get("/second/")]);
    await vi.waitFor(() => expect(otherCalls).toHaveLength(2));
    refresh.resolve();
    await expect(requests).resolves.toHaveLength(2);

    expect(refreshCalls).toHaveLength(1);
    expect(otherCalls.map(bearer)).toEqual([
      `Bearer ${stale}`,
      `Bearer ${stale}`,
      `Bearer ${fresh}`,
      `Bearer ${fresh}`,
    ]);
  });

  it("answers a late 401 for an already replaced access without another refresh", async () => {
    const stale = jwt("s1");
    const fresh = jwt("s1");
    startSession(stale);
    const lateResponse = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: (config) => ok(config, { access: fresh }),
      other: async (config) => {
        if (bearer(config) !== `Bearer ${stale}`) return ok(config);
        if (config.url === "/late/") await lateResponse.promise;
        return failure(config, 401);
      },
    });

    const late = api.get("/late/");
    await vi.waitFor(() => expect(otherCalls).toHaveLength(1));
    await api.get("/early/");
    lateResponse.resolve();
    await late;

    expect(refreshCalls).toHaveLength(1);
    expect(otherCalls.filter((config) => config.url === "/late/").map(bearer)).toEqual([
      `Bearer ${stale}`,
      `Bearer ${fresh}`,
    ]);
  });

  it("keeps the session hint untouched across a refresh rotation", async () => {
    startSession(jwt("s1"));
    // Тот же вход, записанный иначе: любая перезапись подсказки была бы видна.
    const stored = JSON.stringify({ uid: "7", sid: "s1" });
    localStorage.setItem(HINT_KEY, stored);
    const { refreshCalls } = serve({
      refresh: (config) => ok(config, { access: jwt("s1") }),
      other: (config) => (refreshCalls.length ? ok(config) : failure(config, 401)),
    });

    await api.get("/protected/");

    expect(refreshCalls).toHaveLength(1);
    expect(localStorage.getItem(HINT_KEY)).toBe(stored);
  });

  it("refreshes ahead of time when the access expires within a minute", async () => {
    const expiring = jwt("s1", "7", 30);
    const fresh = jwt("s1");
    startSession(expiring);
    const { refreshCalls, otherCalls } = serve({ refresh: (config) => ok(config, { access: fresh }) });

    await api.get("/protected/");

    expect(refreshCalls).toHaveLength(1);
    expect(bearer(otherCalls[0])).toBe(`Bearer ${fresh}`);
  });

  it("keeps using a still-valid access when an early refresh fails transiently", async () => {
    const expiring = jwt("s1", "7", 30);
    startSession(expiring);
    const { otherCalls } = serve({ refresh: (config) => failure(config, 503) });

    await api.get("/protected/");

    expect(bearer(otherCalls[0])).toBe(`Bearer ${expiring}`);
    expect(hasSession()).toBe(true);
  });

  it("runs the refresh inside the cross-tab Web Lock", async () => {
    let held = false;
    const request = setLocks(async (_name, task) => {
      held = true;
      try {
        return await task();
      } finally {
        held = false;
      }
    });
    localStorage.setItem(HINT_KEY, hint("s1"));
    let heldDuringRefresh = false;
    serve({
      refresh: (config) => {
        heldDuringRefresh = held;
        return ok(config, { access: jwt("s1") });
      },
    });

    await api.get("/protected/");

    expect(request).toHaveBeenCalledWith("asyl-auth-refresh", expect.any(Function));
    expect(heldDuringRefresh).toBe(true);
  });

  it("does not send a refresh queued behind the lock once the session changed", async () => {
    const release = Promise.withResolvers<void>();
    const request = setLocks(async (_name, task) => {
      await release.promise;
      return task();
    });
    localStorage.setItem(HINT_KEY, hint("s1"));
    const { refreshCalls } = serve({ refresh: (config) => ok(config, { access: jwt("s1") }) });

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    endSession();
    release.resolve();

    expect(axios.isCancel(await outcome)).toBe(true);
    expect(refreshCalls).toHaveLength(0);
  });

  it("does not abort an in-flight refresh on logout and discards its result", async () => {
    startSession(jwt("s1"));
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return ok(config, { access: jwt("s1") });
      },
      other: (config) => failure(config, 401),
    });

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(refreshCalls).toHaveLength(1));
    endSession();
    refresh.resolve();

    expect(axios.isCancel(await outcome)).toBe(true);
    expect(refreshCalls[0].signal).toBeUndefined();
    expect(otherCalls).toHaveLength(1);
    expect(hasSession()).toBe(false);
  });

  it("does not let an old refresh overwrite a newly established session", async () => {
    startSession(jwt("s1"));
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return ok(config, { access: jwt("s1") });
      },
      other: (config) => (config.url === "/protected/" ? failure(config, 401) : ok(config)),
    });

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(refreshCalls).toHaveLength(1));
    const next = jwt("s2", "8");
    startSession(next);
    refresh.resolve();

    expect(axios.isCancel(await outcome)).toBe(true);
    await api.get("/after/");
    expect(bearer(otherCalls.at(-1)!)).toBe(`Bearer ${next}`);
    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s2", "8"));
  });

  it("does not refresh or retry a late 401 from an older session", async () => {
    startSession(jwt("s1"));
    const oldResponse = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      other: async (config) => {
        await oldResponse.promise;
        return failure(config, 401);
      },
    });

    const outcome = api.post("/loader/orders/1/dispatch/", {}).catch((error: unknown) => error);
    await vi.waitFor(() => expect(otherCalls).toHaveLength(1));
    startSession(jwt("s2", "8"));
    oldResponse.resolve();

    expect(axios.isCancel(await outcome)).toBe(true);
    expect(refreshCalls).toHaveLength(0);
    expect(otherCalls).toHaveLength(1);
  });

  it("never sends a request with another login's access and asks the tab to resync", async () => {
    const listener = vi.fn();
    const unsubscribe = onSessionChange(listener);
    startSession(jwt("s1"));
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls, otherCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return ok(config, { access: jwt("s2", "8") });
      },
      other: (config) => failure(config, 401),
    });

    const outcome = api.post("/payments/", { amount: 1 }).catch((error: unknown) => error);
    await vi.waitFor(() => expect(refreshCalls).toHaveLength(1));
    // Другая вкладка вошла под другим пользователем: cookie и подсказка уже её.
    localStorage.setItem(HINT_KEY, hint("s2", "8"));
    refresh.resolve();

    expect(axios.isCancel(await outcome)).toBe(true);
    expect(otherCalls).toHaveLength(1);
    await vi.waitFor(() => expect(listener).toHaveBeenCalledTimes(1));
    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s2", "8"));
    unsubscribe();
  });

  it("cancels a request when another tab switched the session", async () => {
    const listener = vi.fn();
    const unsubscribe = onSessionChange(listener);
    startSession(jwt("s1"));
    localStorage.setItem(HINT_KEY, hint("s2", "8"));
    const { otherCalls } = serve({});

    expect(axios.isCancel(await api.get("/protected/").catch((error: unknown) => error))).toBe(true);

    expect(otherCalls).toHaveLength(0);
    await vi.waitFor(() => expect(listener).toHaveBeenCalledTimes(1));
    unsubscribe();
  });

  it("ends the session when the cookie belongs to a login nobody announced", async () => {
    window.history.replaceState(null, "", "/login");
    startSession(jwt("s1"));
    const { otherCalls } = serve({
      refresh: (config) => ok(config, { access: jwt("s9", "9") }),
      other: (config) => failure(config, 401),
    });

    expect(axios.isCancel(await api.get("/protected/").catch((error: unknown) => error))).toBe(true);

    expect(otherCalls).toHaveLength(1);
    expect(hasSession()).toBe(false);
  });

  it.each([
    "token_not_valid",
    "no_active_account",
    "password_changed",
    "password_change_required",
    "no_session",
    "not_authenticated",
  ])("ends the session on a refresh 401 %s", async (code) => {
    window.history.replaceState(null, "", "/login");
    startSession(jwt("s1"));
    serve({
      refresh: (config) => failure(config, 401, { detail: "no", code }),
      other: (config) => failure(config, 401),
    });

    await expect(api.get("/protected/")).rejects.toMatchObject({ response: { status: 401, data: { code } } });

    expect(hasSession()).toBe(false);
  });

  it("does not remove a newer login when an old session's refresh is rejected", async () => {
    window.history.replaceState(null, "", "/login");
    startSession(jwt("s1"));
    const refresh = Promise.withResolvers<void>();
    const { refreshCalls } = serve({
      refresh: async (config) => {
        await refresh.promise;
        return failure(config, 401, { detail: "blacklisted", code: "token_not_valid" });
      },
      other: (config) => failure(config, 401),
    });

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(refreshCalls).toHaveLength(1));
    localStorage.setItem(HINT_KEY, hint("s2", "8"));
    refresh.resolve();
    await outcome;

    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s2", "8"));
  });

  it.each([
    ["403 bad_origin", (config: InternalAxiosRequestConfig) => failure(config, 403, { code: "bad_origin" })],
    ["429", (config: InternalAxiosRequestConfig) => failure(config, 429, { code: "throttled" })],
    ["503", (config: InternalAxiosRequestConfig) => failure(config, 503)],
    ["401 without a session verdict", (config: InternalAxiosRequestConfig) => failure(config, 401, { code: "x" })],
    [
      "network",
      (config: InternalAxiosRequestConfig) => {
        throw new AxiosError("Network Error", "ERR_NETWORK", config);
      },
    ],
  ])("keeps the session after a %s refresh failure and exposes it", async (_label, refresh) => {
    const stale = jwt("s1");
    startSession(stale);
    const { otherCalls } = serve({ refresh, other: (config) => failure(config, 401) });

    const error = (await api.get("/protected/").catch((reason: unknown) => reason)) as AxiosError;

    expect(error.config?.url).toBe("/auth/refresh/");
    expect(otherCalls).toHaveLength(1);
    expect(localStorage.getItem(HINT_KEY)).toBe(hint("s1"));
  });

  it("never refreshes after a 401 from a skipAuth request such as a wrong password", async () => {
    const access = jwt("s1");
    startSession(access);
    const { refreshCalls, otherCalls } = serve({ other: (config) => failure(config, 401) });

    await expect(
      api.post("/auth/login/", { username: "u", password: "bad" }, { skipAuth: true }),
    ).rejects.toMatchObject({ response: { status: 401 } });

    expect(refreshCalls).toHaveLength(0);
    expect(bearer(otherCalls[0])).toBeUndefined();
  });
});

describe("login and logout requests", () => {
  it("opens a session under the auth lock without sending the current access", async () => {
    const request = setLocks((_name, task) => task());
    startSession(jwt("s1"));
    const issued = jwt("s2", "8");
    const { otherCalls } = serve({ other: (config) => ok(config, { access: issued }) });

    await expect(requestSession("/auth/login/", { username: "u", password: "p" })).resolves.toBe(issued);

    expect(request).toHaveBeenCalledWith("asyl-auth-refresh", expect.any(Function));
    expect(otherCalls[0].url).toBe("/auth/login/");
    expect(bearer(otherCalls[0])).toBeUndefined();
    expect(otherCalls[0].withCredentials).toBe(true);
  });

  it("revokes the server session under the auth lock and never throws", async () => {
    const request = setLocks((_name, task) => task());
    startSession(jwt("s1"));
    const { otherCalls } = serve({ other: (config) => failure(config, 503) });

    await expect(revokeServerSession()).resolves.toBeUndefined();

    expect(request).toHaveBeenCalledWith("asyl-auth-refresh", expect.any(Function));
    expect(otherCalls[0]).toMatchObject({ url: "/auth/logout/", method: "post", data: "{}", timeout: 5_000 });
    expect(bearer(otherCalls[0])).toBeUndefined();
  });
});
