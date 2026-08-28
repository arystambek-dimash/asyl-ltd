import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useModelTestRunner } from "@/lib/use-model-test-runner";

const modelApi = vi.hoisted(() => ({
  getModelTestInfo: vi.fn(),
  getModelTestJob: vi.fn(),
  startModelTest: vi.fn(),
}));

vi.mock("@/lib/model-tests", () => modelApi);
vi.mock("@/lib/api", () => ({
  apiError: () => "request failed",
  isCanceledRequest: () => false,
}));

const jobId = "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19";
const options = {
  bundle: "production",
  line: { x1: 0, y1: 0.5, x2: 1, y2: 0.5 },
  direction: "any" as const,
  inferenceFps: 12,
};

function job(status: "running" | "completed") {
  return {
    job_id: jobId,
    status,
    created_at: "2026-08-28T00:00:00.000Z",
    started_at: "2026-08-28T00:00:01.000Z",
    finished_at: status === "completed" ? "2026-08-28T00:00:02.000Z" : null,
    bundle_id: "production",
    config: { line: "0,0.5,1,0.5", direction: "any", inference_fps: 12, device: "cpu" },
    progress: {
      decoded_frames: status === "completed" ? 100 : 30,
      processed_frames: status === "completed" ? 50 : 15,
      percent: status === "completed" ? 100 : 30,
    },
    events: [],
    page: { after_event: 0, limit: 500, next_after_event: 0, has_more: false, total_events: 0 },
    error: null,
  };
}

describe("useModelTestRunner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    modelApi.getModelTestInfo.mockReset().mockResolvedValue({
      enabled: true,
      bundles: [],
      defaults: { line: "0,0.5,1,0.5", direction: "any", inference_fps: 12 },
      limits: { max_upload_bytes: 100 },
      device: "cpu",
      reject_while_processors_active: true,
      active_processors: 0,
    });
    modelApi.getModelTestJob.mockReset();
    modelApi.startModelTest.mockReset().mockResolvedValue({
      job_id: jobId,
      status: "queued",
      status_url: `/api/cameras/model-tests/${jobId}/`,
      bundle: "production",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("serializes polling and stops after a terminal response", async () => {
    let resolveFirst: (value: ReturnType<typeof job>) => void = () => undefined;
    const first = new Promise<ReturnType<typeof job>>((resolve) => {
      resolveFirst = resolve;
    });
    modelApi.getModelTestJob.mockImplementationOnce(() => first).mockResolvedValueOnce(job("completed"));
    const { result } = renderHook(() => useModelTestRunner());
    await act(async () => Promise.resolve());

    const file = new File(["video"], "test.mp4", { type: "video/mp4" });
    await act(async () => {
      await result.current.start(file, options);
    });
    await act(async () => Promise.resolve());
    expect(modelApi.getModelTestJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(modelApi.getModelTestJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst(job("running"));
      await Promise.resolve();
    });
    expect(result.current.job?.status).toBe("running");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });
    expect(modelApi.getModelTestJob).toHaveBeenCalledTimes(2);
    expect(result.current.job?.status).toBe("completed");
    expect(sessionStorage.getItem("asyl:model-test-active:v1")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(modelApi.getModelTestJob).toHaveBeenCalledTimes(2);
  });

  it("restores only a minimal active job id after refresh", async () => {
    sessionStorage.setItem("asyl:model-test-active:v1", jobId);
    modelApi.getModelTestJob.mockResolvedValue(job("completed"));
    const { result } = renderHook(() => useModelTestRunner());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(modelApi.getModelTestJob).toHaveBeenCalledWith(jobId, 0, 100, expect.any(AbortSignal));
    expect(result.current.job?.status).toBe("completed");
  });
});
