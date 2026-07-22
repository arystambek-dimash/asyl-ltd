import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, clearTokens, setTokens } from "@/lib/api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function unauthorized(config: InternalAxiosRequestConfig): never {
  const response = {
    config,
    data: { detail: "expired" },
    headers: {},
    status: 401,
    statusText: "Unauthorized",
  } as AxiosResponse;
  throw new AxiosError("Unauthorized", "ERR_BAD_REQUEST", config, undefined, response);
}

describe("auth refresh generation", () => {
  const originalAdapter = api.defaults.adapter;

  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    clearTokens();
  });

  it("does not restore an access token when refresh resolves after logout", async () => {
    setTokens("expired-access", "refresh-one");
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => unauthorized(config));
    api.defaults.adapter = adapter;

    const refresh = deferred<AxiosResponse<{ access: string }>>();
    vi.spyOn(axios, "post").mockReturnValue(refresh.promise);

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));

    clearTokens();
    refresh.resolve({
      data: { access: "late-access" },
      status: 200,
      statusText: "OK",
      headers: {},
      config: {} as InternalAxiosRequestConfig,
    });

    const error = await outcome;
    expect(axios.isCancel(error)).toBe(true);
    expect(localStorage.getItem("asyl_access")).toBeNull();
    expect(localStorage.getItem("asyl_refresh")).toBeNull();
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it("keeps refresh single-flight for concurrent 401 responses", async () => {
    setTokens("expired-access", "refresh-one");
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig & { _retry?: boolean }) => {
      if (!config._retry) unauthorized(config);
      return {
        config,
        data: { ok: true },
        headers: {},
        status: 200,
        statusText: "OK",
      } as AxiosResponse;
    });
    api.defaults.adapter = adapter;

    const refresh = deferred<AxiosResponse<{ access: string }>>();
    vi.spyOn(axios, "post").mockReturnValue(refresh.promise);

    const first = api.get("/first/");
    const second = api.get("/second/");
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    refresh.resolve({
      data: { access: "fresh-access" },
      status: 200,
      statusText: "OK",
      headers: {},
      config: {} as InternalAxiosRequestConfig,
    });

    await expect(Promise.all([first, second])).resolves.toEqual([
      expect.objectContaining({ data: { ok: true } }),
      expect.objectContaining({ data: { ok: true } }),
    ]);
    expect(adapter).toHaveBeenCalledTimes(4);
    expect(localStorage.getItem("asyl_access")).toBe("fresh-access");
  });

  it("does not let an old refresh overwrite a newly established session", async () => {
    setTokens("expired-access", "refresh-one");
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => unauthorized(config));

    const refresh = deferred<AxiosResponse<{ access: string }>>();
    vi.spyOn(axios, "post").mockReturnValue(refresh.promise);

    const outcome = api.get("/protected/").catch((error: unknown) => error);
    await vi.waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));

    setTokens("new-session-access", "refresh-two");
    refresh.resolve({
      data: { access: "late-old-access" },
      status: 200,
      statusText: "OK",
      headers: {},
      config: {} as InternalAxiosRequestConfig,
    });

    expect(axios.isCancel(await outcome)).toBe(true);
    expect(localStorage.getItem("asyl_access")).toBe("new-session-access");
    expect(localStorage.getItem("asyl_refresh")).toBe("refresh-two");
  });

  it("does not refresh or retry a late 401 from an older session", async () => {
    setTokens("session-a-access", "session-a-refresh");
    const refreshSpy = vi.spyOn(axios, "post");
    const oldResponse = deferred<void>();
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      await oldResponse.promise;
      return unauthorized(config);
    });
    api.defaults.adapter = adapter;

    const outcome = api.post("/orders/1/finish-loading/", {}).catch((error: unknown) => error);
    await vi.waitFor(() => expect(adapter).toHaveBeenCalledTimes(1));

    setTokens("session-b-access", "session-b-refresh");
    oldResponse.resolve();

    const error = await outcome;
    expect(axios.isCancel(error)).toBe(true);
    expect(refreshSpy).not.toHaveBeenCalled();
    expect(adapter).toHaveBeenCalledTimes(1);
  });
});
