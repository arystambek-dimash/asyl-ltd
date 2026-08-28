import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ModelTestJob, ModelTestModelInfo } from "@/lib/model-tests";
import { ModelTestResults } from "./model-test-results";

const model: ModelTestModelInfo = {
  loaded: true,
  role: "classifier",
  id: "brand_classifier.pt",
  sha256: "a".repeat(64),
  version: "8.3.0",
  device: "cpu",
  input_size: 224,
  fp16: false,
  classes: ["dikhan_baba", "korol"],
  warmup_runs: 2,
  load_seconds: 0.2,
  instances: 1,
};

const completed: ModelTestJob = {
  job_id: "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
  status: "completed",
  created_at: "2026-08-28T00:00:00.000Z",
  started_at: "2026-08-28T00:00:01.000Z",
  finished_at: "2026-08-28T00:00:04.000Z",
  bundle_id: "production",
  config: { line: "0,0.5,1,0.5", direction: "any", inference_fps: 12, device: "cpu" },
  progress: { decoded_frames: 120, processed_frames: 60, percent: 100 },
  events: [
    {
      index: 1,
      video_time_sec: 1.25,
      frame: 30,
      track_id: 7,
      bbox: [10, 20, 110, 220],
      point: [60, 220],
      class_id: 1,
      class_name: "Green_50",
      confidence: 0.94,
      weight_kg: 50,
      direction: "positive",
      color: "Green",
      color_confidence: 0.99,
      brand: "korol",
      brand_confidence: 0.88,
      sku: "korol_green_50",
      classification_status: "recognized",
    },
  ],
  page: { after_event: 0, limit: 100, next_after_event: 1, has_more: false, total_events: 1 },
  error: null,
  bundle: {
    id: "production",
    detector: { ...model, role: "detector", id: "detector.pt", input_size: 640, classes: ["Green_50"] },
    classifiers: {
      loaded: true,
      role: "classification",
      device: "cpu",
      fp16: false,
      brand_confidence_threshold: 0.5,
      color_model: { ...model, id: "color_classifier.pt", classes: ["Green_50"] },
      brand_model: model,
    },
  },
  input: {
    sha256: "b".repeat(64),
    size_bytes: 1_048_576,
    content_type: "video/mp4",
    width: 1920,
    height: 1080,
    fps: 24,
    frame_count: 120,
    duration_seconds: 5,
  },
  summary: {
    total: 1,
    total_weight_kg: 50,
    per_detector_class: { Green_50: 1 },
    per_classified_color: { Green: 1 },
    per_brand: { korol: 1 },
    per_sku: { korol_green_50: 1 },
    per_classification_status: { recognized: 1 },
    detector_observations_by_class: { Green_50: 4 },
    detections: 4,
    decoded_frames: 120,
    processed_frames: 60,
    detector_inference_avg_ms: 12,
    detector_inference_p95_ms: 18,
    classification_avg_ms: 3,
    classification_errors: 0,
    elapsed_seconds: 3,
  },
};

describe("ModelTestResults", () => {
  it("shows model provenance, all distributions and seeks through an event callback", async () => {
    const user = userEvent.setup();
    const onSelectEvent = vi.fn();
    render(
      <ModelTestResults
        job={completed}
        selectedEvent={null}
        onSelectEvent={onSelectEvent}
        loadingMore={false}
        onLoadMore={vi.fn()}
      />,
    );

    expect(screen.getByText("Brand classifier")).toBeInTheDocument();
    expect(screen.getByText("brand_classifier.pt")).toBeInTheDocument();
    expect(screen.getByText("Проверенный входной файл")).toBeInTheDocument();
    expect(screen.getByText("Статус классификации")).toBeInTheDocument();
    expect(screen.getAllByText("korol_green_50").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /#1/ }));
    expect(onSelectEvent).toHaveBeenCalledWith(completed.events[0]);
  });
});
