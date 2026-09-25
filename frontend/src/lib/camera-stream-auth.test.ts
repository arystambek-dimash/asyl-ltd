import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: { post: mocks.post },
}));

import { ensureCameraStreamToken, invalidateCameraStreamToken } from "@/lib/camera-stream-auth";

describe("camera stream authorization cache", () => {
  beforeEach(() => {
    invalidateCameraStreamToken();
  });

  it("shares one token request between camera tiles", async () => {
    const request = Promise.withResolvers<unknown>();
    mocks.post.mockReturnValueOnce(request.promise);

    const first = ensureCameraStreamToken();
    const second = ensureCameraStreamToken();
    expect(second).toBe(first);

    request.resolve({});
    await Promise.all([first, second]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("rejects a late token response after invalidation and fetches again", async () => {
    const oldRequest = Promise.withResolvers<unknown>();
    mocks.post.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce({});

    const oldToken = ensureCameraStreamToken();
    invalidateCameraStreamToken();
    oldRequest.resolve({});

    await expect(oldToken).rejects.toMatchObject({ code: "ERR_CANCELED" });
    await ensureCameraStreamToken();
    expect(mocks.post).toHaveBeenCalledTimes(2);
  });
});
