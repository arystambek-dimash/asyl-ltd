import type { AxiosProgressEvent } from "axios";

import { api } from "@/lib/api";
import type { LineDirection, NormalizedLine } from "@/lib/camera-counting-line";

export const MODEL_TEST_ACCEPT = ".mp4,.mov,.avi,.mkv,video/mp4,video/quicktime,video/x-msvideo,video/x-matroska";

const CONTENT_TYPES = new Set([
  "application/octet-stream",
  "video/mp4",
  "video/quicktime",
  "video/x-matroska",
  "video/x-msvideo",
]);

const EXTENSION_CONTENT_TYPE: Record<string, string> = {
  ".avi": "video/x-msvideo",
  ".mkv": "video/x-matroska",
  ".mov": "video/quicktime",
  ".mp4": "video/mp4",
};

export type ModelTestStatus = "queued" | "running" | "completed" | "failed";

export interface ModelTestBundleOption {
  id: string;
  ready: boolean;
  detector: string;
  color_classifier: string;
  brand_classifier: string;
}

export interface ModelTestInfo {
  enabled: boolean;
  bundles: ModelTestBundleOption[];
  defaults: {
    line: string;
    direction: LineDirection;
    inference_fps: number;
  };
  limits: {
    max_upload_bytes: number;
    max_duration_seconds?: number;
    max_inference_fps?: number;
    max_processed_frames?: number;
    timeout_seconds?: number;
    max_pending_jobs?: number;
    job_ttl_seconds?: number;
    max_retained_jobs?: number;
  };
  device: string;
  reject_while_processors_active: boolean;
  active_processors: number;
  running_job_id?: string | null;
  pending_jobs?: number;
  retained_jobs?: number;
  writes_production_analytics?: false;
}

export interface ModelTestAccepted {
  job_id: string;
  status: "queued";
  status_url: string;
  bundle: string;
}

export interface ModelTestEvent {
  index: number;
  video_time_sec: number;
  frame: number;
  track_id: number;
  bbox: [number, number, number, number];
  point: [number, number];
  class_id: number;
  class_name: string;
  confidence: number;
  weight_kg: number;
  direction: string;
  color: string;
  color_confidence: number | null;
  brand: string;
  brand_confidence: number | null;
  sku: string;
  classification_status: string;
}

export interface ModelTestModelInfo {
  loaded: boolean;
  role: string;
  id: string;
  sha256: string;
  version: string;
  device: string;
  input_size: number;
  fp16: boolean;
  classes: string[];
  warmup_runs: number;
  load_seconds: number;
  instances: number;
}

export interface ModelTestRuntimeBundle {
  id: string;
  detector: ModelTestModelInfo & { warmup_shapes?: [number, number][] };
  classifiers: {
    loaded: boolean;
    role: string;
    device: string;
    fp16: boolean;
    brand_confidence_threshold: number;
    color_model: ModelTestModelInfo;
    brand_model: ModelTestModelInfo;
  };
}

export interface ModelTestSummary {
  total: number;
  total_weight_kg: number;
  per_detector_class: Record<string, number>;
  per_classified_color: Record<string, number>;
  per_brand: Record<string, number>;
  per_sku: Record<string, number>;
  per_classification_status: Record<string, number>;
  detector_observations_by_class: Record<string, number>;
  detections: number;
  decoded_frames: number;
  processed_frames: number;
  detector_inference_avg_ms: number;
  detector_inference_p95_ms: number;
  classification_avg_ms: number;
  classification_errors: number;
  elapsed_seconds: number;
}

export interface ModelTestJob {
  job_id: string;
  status: ModelTestStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  bundle_id: string;
  config: {
    line: string;
    direction: "any" | "positive" | "negative";
    inference_fps: number;
    device: string;
  };
  progress: {
    decoded_frames: number;
    processed_frames: number;
    percent: number;
  };
  events: ModelTestEvent[];
  page: {
    after_event: number;
    limit: number;
    next_after_event: number;
    has_more: boolean;
    total_events: number;
  };
  error: { code: string; message: string } | null;
  bundle?: ModelTestRuntimeBundle;
  input?: {
    sha256: string;
    size_bytes: number;
    content_type: string;
    width: number;
    height: number;
    fps: number | null;
    frame_count: number;
    duration_seconds: number | null;
  };
  summary?: ModelTestSummary;
}

export interface ModelTestStartOptions {
  bundle: string;
  line: NormalizedLine;
  direction: LineDirection;
  inferenceFps: number;
}

export interface KnownModelMetadata {
  name: string;
  metric: string;
  note: string;
}

const KNOWN_MODELS: Record<string, KnownModelMetadata> = {
  "brand_classifier.pt": {
    name: "brand-cls-session-v3",
    metric: "85.9% · честный тест",
    note: "Бренд определяется без опоры на цвет.",
  },
  "color_classifier.pt": {
    name: "color-cls-v2",
    metric: "99.7% · validation",
    note: "Исправляет случай «зелёный Korol = синий».",
  },
  "detector.pt": {
    name: "YOLO26-nano",
    metric: "7 классов · включая White_50",
    note: "Обучен на сценах robot / gazel / pov2.",
  },
};

function extension(name: string): string {
  const match = /\.[^.]+$/.exec(name.toLowerCase());
  return match?.[0] ?? "";
}

export function knownModelMetadata(fileName: string): KnownModelMetadata | null {
  const baseName = fileName.split(/[\\/]/).at(-1)?.toLowerCase() ?? "";
  return KNOWN_MODELS[baseName] ?? null;
}

export function modelTestContentType(file: File): string {
  const normalized = file.type.split(";", 1)[0]?.trim().toLowerCase();
  if (normalized && CONTENT_TYPES.has(normalized)) return normalized;
  return EXTENSION_CONTENT_TYPE[extension(file.name)] ?? "application/octet-stream";
}

export function validateModelTestFile(file: File, maxBytes: number): string {
  if (file.size <= 0) return "Видео не должно быть пустым";
  if (file.size > maxBytes) return `Видео больше лимита ${formatBytes(maxBytes)}`;
  const normalizedType = file.type.split(";", 1)[0]?.trim().toLowerCase();
  const specificSupportedType = normalizedType !== "application/octet-stream" && CONTENT_TYPES.has(normalizedType);
  if (!EXTENSION_CONTENT_TYPE[extension(file.name)] && !specificSupportedType) {
    return "Поддерживаются только MP4, MOV, AVI и MKV";
  }
  return "";
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${units[unit]}`;
}

export function serializeModelTestLine(line: NormalizedLine): string {
  return [line.x1, line.y1, line.x2, line.y2].map((coordinate) => Number(coordinate.toFixed(6)).toString()).join(",");
}

export async function getModelTestInfo(signal?: AbortSignal): Promise<ModelTestInfo> {
  const response = await api.get<ModelTestInfo>("/cameras/model-tests/", { signal });
  return response.data;
}

export async function startModelTest(
  file: File,
  options: ModelTestStartOptions,
  onProgress: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<ModelTestAccepted> {
  const response = await api.post<ModelTestAccepted>("/cameras/model-tests/", file, {
    headers: { "Content-Type": modelTestContentType(file) },
    params: {
      bundle: options.bundle,
      line: serializeModelTestLine(options.line),
      direction: options.direction,
      inference_fps: options.inferenceFps,
    },
    signal,
    onUploadProgress: (event: AxiosProgressEvent) => {
      onProgress(event.loaded, event.total ?? file.size);
    },
  });
  return response.data;
}

export async function getModelTestJob(
  jobId: string,
  afterEvent = 0,
  limit = 100,
  signal?: AbortSignal,
): Promise<ModelTestJob> {
  const response = await api.get<ModelTestJob>(`/cameras/model-tests/${jobId}/`, {
    params: { after_event: afterEvent, limit },
    signal,
  });
  return response.data;
}
