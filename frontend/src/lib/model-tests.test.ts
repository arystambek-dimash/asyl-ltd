import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatBytes,
  knownModelMetadata,
  modelTestContentType,
  serializeModelTestLine,
  startModelTest,
  validateModelTestFile,
} from "@/lib/model-tests";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({ api }));

describe("model test browser contract", () => {
  beforeEach(() => {
    api.get.mockReset();
    api.post.mockReset();
  });

  it("validates supported files and the server size limit", () => {
    const mp4 = new File(["video"], "hypothesis.mp4", { type: "video/mp4" });
    const mkv = new File(["video"], "hypothesis.mkv");
    const webm = new File(["video"], "hypothesis.webm", { type: "video/webm" });
    const genericWebm = new File(["video"], "hypothesis.webm", { type: "application/octet-stream" });

    expect(validateModelTestFile(mp4, 100)).toBe("");
    expect(validateModelTestFile(mkv, 100)).toBe("");
    expect(modelTestContentType(mkv)).toBe("video/x-matroska");
    expect(validateModelTestFile(mp4, 2)).toContain(formatBytes(2));
    expect(validateModelTestFile(webm, 100)).toContain("MP4, MOV, AVI и MKV");
    expect(validateModelTestFile(genericWebm, 100)).toContain("MP4, MOV, AVI и MKV");
  });

  it("posts the File as a raw body with allowlisted query parameters", async () => {
    const file = new File(["1234"], "test.mov", { type: "video/quicktime" });
    const progress = vi.fn();
    api.post.mockImplementation(async (_url, _body, config) => {
      config.onUploadProgress({ loaded: 4, total: 4 });
      return { data: { job_id: "job", status: "queued", status_url: "/job", bundle: "candidate" } };
    });

    await startModelTest(
      file,
      {
        bundle: "candidate",
        line: { x1: 0, y1: 0.12345678, x2: 1, y2: 0.5 },
        direction: "negative",
        inferenceFps: 8,
      },
      progress,
    );

    expect(api.post).toHaveBeenCalledWith(
      "/cameras/model-tests/",
      file,
      expect.objectContaining({
        headers: { "Content-Type": "video/quicktime" },
        params: {
          bundle: "candidate",
          line: "0,0.123457,1,0.5",
          direction: "negative",
          inference_fps: 8,
        },
      }),
    );
    expect(progress).toHaveBeenCalledWith(4, 4);
  });

  it("exposes the three known production model identities", () => {
    expect(knownModelMetadata("detector.pt")?.name).toBe("YOLO26-nano");
    expect(knownModelMetadata("color_classifier.pt")?.metric).toContain("99.7%");
    expect(knownModelMetadata("brand_classifier.pt")?.name).toBe("brand-cls-session-v3");
    expect(knownModelMetadata("brand_classifier.pt")?.metric).toContain("85.9%");
    expect(serializeModelTestLine({ x1: 0, y1: 0.5, x2: 1, y2: 0.5 })).toBe("0,0.5,1,0.5");
  });
});
