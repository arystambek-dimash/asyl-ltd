import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  clearTokens: vi.fn(),
  hasAuthTokens: vi.fn(),
  invalidateAuthSessionRequests: vi.fn(),
  invalidateCameraStreamToken: vi.fn(),
  setTokens: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { get: mocks.apiGet, post: mocks.apiPost },
  clearTokens: mocks.clearTokens,
  hasAuthTokens: mocks.hasAuthTokens,
  invalidateAuthSessionRequests: mocks.invalidateAuthSessionRequests,
  setTokens: mocks.setTokens,
}));

vi.mock("@/lib/camera-stream-auth", () => ({
  invalidateCameraStreamToken: mocks.invalidateCameraStreamToken,
}));

import { useAuth } from "@/store/auth";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function me(id: number, username: string): Me {
  return {
    id,
    username,
    is_client: false,
    is_superuser: false,
    is_monoblock: false,
    monoblock_name: null,
    monoblock_camera: null,
    permissions: [],
    role_name: null,
    client_id: null,
    sales_department: null,
  };
}

describe("auth store generations", () => {
  beforeEach(() => {
    useAuth.getState().logout();
    useAuth.setState({ me: null, loading: true });
    vi.clearAllMocks();
    mocks.hasAuthTokens.mockReturnValue(true);
  });

  it("does not request /me when no auth token exists", async () => {
    mocks.hasAuthTokens.mockReturnValue(false);

    await useAuth.getState().loadMe();

    expect(mocks.apiGet).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("ignores an old loadMe response after logout and a new login", async () => {
    const oldMe = me(1, "old-user");
    const newMe = me(2, "new-user");
    const oldRequest = deferred<{ data: Me }>();
    mocks.apiGet.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce({ data: newMe });
    mocks.apiPost.mockResolvedValueOnce({
      data: { access: "new-access", refresh: "new-refresh" },
    });

    const loadingOldSession = useAuth.getState().loadMe();
    useAuth.getState().logout();
    await useAuth.getState().login("new-user", "password");

    oldRequest.resolve({ data: oldMe });
    await loadingOldSession;

    expect(useAuth.getState().me).toEqual(newMe);
    expect(mocks.setTokens).toHaveBeenCalledWith("new-access", "new-refresh");
    expect(mocks.invalidateCameraStreamToken).toHaveBeenCalled();
  });

  it("does not commit a login response that resolves after logout", async () => {
    const loginRequest = deferred<{ data: { access: string; refresh: string } }>();
    mocks.apiPost.mockReturnValueOnce(loginRequest.promise);

    const login = useAuth.getState().login("late-user", "password");
    useAuth.getState().logout();
    loginRequest.resolve({ data: { access: "late-access", refresh: "late-refresh" } });

    await expect(login).rejects.toMatchObject({ code: "ERR_CANCELED" });
    expect(mocks.setTokens).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
  });

  it("adopts registration tokens as a new session even when a user is loaded", async () => {
    const previous = me(1, "staff-user");
    const registered = { ...me(2, "client-user"), is_client: true };
    useAuth.setState({ me: previous, loading: false });
    mocks.apiGet.mockResolvedValueOnce({ data: registered });

    await useAuth.getState().adoptSession("client-access", "client-refresh");

    expect(mocks.setTokens).toHaveBeenCalledWith("client-access", "client-refresh");
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/auth/me/",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    );
    expect(useAuth.getState().me).toEqual(registered);
  });

  it("synchronizes a replacement from another tab without clearing shared tokens", async () => {
    const external = me(2, "external-user");
    useAuth.setState({ me: me(1, "current-user"), loading: false });
    mocks.apiGet.mockResolvedValueOnce({ data: external });

    await useAuth.getState().syncExternalSession();

    expect(mocks.invalidateAuthSessionRequests).toHaveBeenCalledTimes(1);
    expect(mocks.setTokens).not.toHaveBeenCalled();
    expect(mocks.clearTokens).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toEqual(external);
  });

  it("throttles refreshMe only after a successful /me response", async () => {
    const current = me(1, "current");
    const updated = me(1, "updated");
    useAuth.setState({ me: current, loading: false });
    mocks.apiGet.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: updated });

    await useAuth.getState().refreshMe();
    await useAuth.getState().refreshMe();
    await useAuth.getState().refreshMe();

    expect(mocks.apiGet).toHaveBeenCalledTimes(2);
    expect(useAuth.getState().me).toEqual(updated);
  });
});
