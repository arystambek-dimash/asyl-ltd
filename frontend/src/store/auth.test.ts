import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";
import { makeMe } from "@/test-utils/factories";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  endSession: vi.fn(),
  hasSession: vi.fn(),
  invalidateAuthSessionRequests: vi.fn(),
  invalidateCameraStreamToken: vi.fn(),
  requestSession: vi.fn(),
  revokeServerSession: vi.fn(),
  startSession: vi.fn(),
}));

vi.mock("@/lib/api", async () => ({
  api: { get: mocks.apiGet },
  staleAuthSession: (await vi.importActual<typeof import("@/lib/api")>("@/lib/api")).staleAuthSession,
  endSession: mocks.endSession,
  hasSession: mocks.hasSession,
  invalidateAuthSessionRequests: mocks.invalidateAuthSessionRequests,
  requestSession: mocks.requestSession,
  revokeServerSession: mocks.revokeServerSession,
  startSession: mocks.startSession,
}));

vi.mock("@/lib/camera-stream-auth", () => ({
  invalidateCameraStreamToken: mocks.invalidateCameraStreamToken,
}));

import { useAuth } from "@/store/auth";

function responseError(status: number) {
  return { response: { status } };
}

describe("auth store generations", () => {
  beforeEach(() => {
    useAuth.getState().logout();
    useAuth.setState({ me: null, loading: true });
    vi.clearAllMocks();
    mocks.hasSession.mockReturnValue(true);
  });

  it("does not request /me when the browser has no session", async () => {
    mocks.hasSession.mockReturnValue(false);

    await useAuth.getState().loadMe();

    expect(mocks.apiGet).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("keeps an initial saved session after a transient /me failure", async () => {
    mocks.apiGet.mockRejectedValueOnce(responseError(503));

    await useAuth.getState().loadMe();

    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("clears an initially rejected saved session", async () => {
    mocks.apiGet.mockRejectedValueOnce(responseError(401));

    await useAuth.getState().loadMe();

    expect(mocks.endSession).toHaveBeenCalledTimes(1);
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("keeps the saved session on a 403 such as a misconfigured Origin check", async () => {
    mocks.apiGet.mockRejectedValueOnce({ response: { status: 403, data: { code: "bad_origin" } } });

    await useAuth.getState().loadMe();

    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("ignores an old loadMe response after logout and a new login", async () => {
    const oldMe = makeMe({ id: 1, username: "old-user" });
    const newMe = makeMe({ id: 2, username: "new-user" });
    const oldRequest = Promise.withResolvers<{ data: Me }>();
    mocks.apiGet.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce({ data: newMe });
    mocks.requestSession.mockResolvedValueOnce("new-access");

    const loadingOldSession = useAuth.getState().loadMe();
    useAuth.getState().logout();
    await useAuth.getState().login("new-user", "password");

    oldRequest.resolve({ data: oldMe });
    await loadingOldSession;

    expect(useAuth.getState().me).toEqual(newMe);
    expect(mocks.requestSession).toHaveBeenCalledWith(
      "/auth/login/",
      { username: "new-user", password: "password" },
      expect.any(AbortSignal),
    );
    expect(mocks.startSession).toHaveBeenCalledWith("new-access");
    expect(mocks.invalidateCameraStreamToken).toHaveBeenCalled();
  });

  it("does not commit a login response that resolves after logout", async () => {
    const loginRequest = Promise.withResolvers<string>();
    mocks.requestSession.mockReturnValueOnce(loginRequest.promise);

    const login = useAuth.getState().login("late-user", "password");
    useAuth.getState().logout();
    loginRequest.resolve("late-access");

    await expect(login).rejects.toMatchObject({ code: "ERR_CANCELED" });
    expect(mocks.startSession).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
  });

  it("changes an initial password and adopts the returned session", async () => {
    const client = makeMe({ id: 2, username: "client-user", is_client: true });
    mocks.requestSession.mockResolvedValueOnce("client-access");
    mocks.apiGet.mockResolvedValueOnce({ data: client });

    await useAuth.getState().completeInitialPasswordChange("client-user", "temporary-password", "personal-password");

    expect(mocks.requestSession).toHaveBeenCalledWith(
      "/auth/initial-password/",
      {
        username: "client-user",
        current_password: "temporary-password",
        new_password: "personal-password",
      },
      expect.any(AbortSignal),
    );
    expect(mocks.startSession).toHaveBeenCalledWith("client-access");
    expect(useAuth.getState().me).toEqual(client);
  });

  it("adopts a registration session even when a user is loaded", async () => {
    const previous = makeMe({ id: 1, username: "staff-user" });
    const registered = makeMe({ id: 2, username: "client-user", is_client: true });
    useAuth.setState({ me: previous, loading: false });
    mocks.apiGet.mockResolvedValueOnce({ data: registered });

    await useAuth.getState().adoptSession("client-access");

    expect(mocks.startSession).toHaveBeenCalledWith("client-access");
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/auth/me/",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    );
    expect(useAuth.getState().me).toEqual(registered);
  });

  it("synchronizes a login from another tab without ending the shared session", async () => {
    const external = makeMe({ id: 2, username: "external-user" });
    useAuth.setState({ me: makeMe({ id: 1, username: "current-user" }), loading: false });
    mocks.apiGet.mockResolvedValueOnce({ data: external });

    await useAuth.getState().syncExternalSession();

    expect(mocks.invalidateAuthSessionRequests).toHaveBeenCalledTimes(1);
    expect(mocks.startSession).not.toHaveBeenCalled();
    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toEqual(external);
  });

  it("does not restore the previous user when an external-session sync fails transiently", async () => {
    useAuth.setState({ me: makeMe({ id: 1, username: "current-user" }), loading: false });
    mocks.apiGet.mockRejectedValueOnce(responseError(503));

    await useAuth.getState().syncExternalSession();

    // Cookie уже от другого входа: прежний пользователь на экране был бы ложью.
    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("clears an externally replaced session rejected by the server", async () => {
    useAuth.setState({ me: makeMe({ id: 1, username: "current-user" }), loading: false });
    mocks.apiGet.mockRejectedValueOnce(responseError(401));

    await useAuth.getState().syncExternalSession();

    expect(mocks.endSession).toHaveBeenCalledTimes(1);
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("keeps the known user after a transient refresh failure", async () => {
    const current = makeMe({ id: 1, username: "current-user" });
    useAuth.setState({ me: current, loading: false });
    mocks.apiGet.mockRejectedValueOnce(new Error("offline"));

    await useAuth.getState().refreshMe(true);

    expect(mocks.endSession).not.toHaveBeenCalled();
    expect(useAuth.getState().me).toEqual(current);
  });

  it("expires a server-rejected session", async () => {
    const current = makeMe({ id: 1, username: "current-user" });
    useAuth.setState({ me: current, loading: false });
    mocks.apiGet.mockRejectedValueOnce(responseError(401));

    await useAuth.getState().refreshMe(true);

    expect(mocks.endSession).toHaveBeenCalledTimes(1);
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });

  it("throttles refreshMe only after a successful /me response", async () => {
    const current = makeMe({ id: 1, username: "current" });
    const updated = makeMe({ id: 1, username: "updated" });
    useAuth.setState({ me: current, loading: false });
    mocks.apiGet.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: updated });

    await useAuth.getState().refreshMe();
    await useAuth.getState().refreshMe();
    await useAuth.getState().refreshMe();

    expect(mocks.apiGet).toHaveBeenCalledTimes(2);
    expect(useAuth.getState().me).toEqual(updated);
  });

  it("signs out on the server first, then locally", async () => {
    const order: string[] = [];
    mocks.revokeServerSession.mockImplementationOnce(async () => {
      order.push("server");
    });
    mocks.endSession.mockImplementationOnce(() => order.push("local"));
    useAuth.setState({ me: makeMe({ id: 1, username: "current-user" }), loading: false });

    await useAuth.getState().signOut();

    expect(order).toEqual(["server", "local"]);
    expect(mocks.invalidateCameraStreamToken).toHaveBeenCalled();
    expect(useAuth.getState().me).toBeNull();
    expect(useAuth.getState().loading).toBe(false);
  });
});
