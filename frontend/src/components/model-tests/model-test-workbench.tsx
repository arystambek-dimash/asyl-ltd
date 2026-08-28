"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, FlaskConical, PencilLine, Play, ShieldCheck, Square } from "lucide-react";

import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { DetectionOverlay } from "@/components/detection-overlay";
import { ModelTestResults } from "@/components/model-tests/model-test-results";
import { VideoDropZone } from "@/components/model-tests/video-drop-zone";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ProgressBar } from "@/components/ui/progress-bar";
import { Select } from "@/components/ui/select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import {
  defaultCountingLine,
  normalizeCountingLine,
  normalizeLineDirection,
  validCountingLine,
  type LineDirection,
  type NormalizedLine,
} from "@/lib/camera-counting-line";
import {
  knownModelMetadata,
  validateModelTestFile,
  type ModelTestBundleOption,
  type ModelTestEvent,
  type ModelTestJob,
} from "@/lib/model-tests";
import { useModelTestRunner } from "@/lib/use-model-test-runner";
import type { AlwaysOnDetection } from "@/lib/types";
import { cn } from "@/lib/utils";

type SavedRun = { job: ModelTestJob; hypothesis: string };

const STATUS_LABELS = {
  queued: "В очереди",
  running: "Обработка",
  completed: "Готово",
  failed: "Ошибка",
} as const;

function ModelFileCard({ role, fileName }: { role: string; fileName: string }) {
  const metadata = knownModelMetadata(fileName);
  return (
    <div className="rounded-lg border bg-[var(--background)] p-3">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
        {role}
      </div>
      <div className="truncate text-sm font-semibold" title={fileName}>
        {metadata?.name ?? fileName}
      </div>
      <div className="mt-1 truncate font-mono text-[11px] text-[var(--muted-foreground)]" title={fileName}>
        {fileName || "не задан"}
      </div>
      {metadata ? (
        <div className="mt-2 text-xs">
          <div className="font-medium text-[var(--ring)]">{metadata.metric}</div>
          <div className="mt-0.5 text-[var(--muted-foreground)]">{metadata.note}</div>
        </div>
      ) : null}
    </div>
  );
}

function BundleDetails({ bundle }: { bundle: ModelTestBundleOption }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <ModelFileCard role="Detector" fileName={bundle.detector} />
      <ModelFileCard role="Color classifier" fileName={bundle.color_classifier} />
      <ModelFileCard role="Brand classifier" fileName={bundle.brand_classifier} />
    </div>
  );
}

function JobProgress({ job, status }: { job: ModelTestJob | null; status: keyof typeof STATUS_LABELS | null }) {
  if (!status) return null;
  const percent = job?.progress.percent ?? 0;
  const tone = status === "completed" ? "success" : status === "failed" ? "destructive" : "primary";
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Badge tone={tone} dot>
              {STATUS_LABELS[status]}
            </Badge>
            <span className="font-mono text-xs text-[var(--muted-foreground)]">
              {job?.job_id ?? "получаем job id…"}
            </span>
          </div>
          <span className="text-sm font-semibold tabular-nums">{Math.round(percent)}%</span>
        </div>
        <ProgressBar pct={percent} className="h-2" />
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--muted-foreground)]">
          <span>Decoded: {job?.progress.decoded_frames ?? 0}</span>
          <span>Processed: {job?.progress.processed_frames ?? 0}</span>
          <span>События появляются после полного расчёта.</span>
        </div>
      </CardContent>
    </Card>
  );
}

function RunComparison({ runs }: { runs: SavedRun[] }) {
  if (runs.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Сравнение гипотез этой вкладки</CardTitle>
        <p className="text-xs text-[var(--muted-foreground)]">
          Результаты camera-PC живут ограниченное время; эта таблица сбросится при обновлении страницы.
        </p>
      </CardHeader>
      <CardContent>
        <Table>
          <THead>
            <TR>
              <TH>Гипотеза</TH>
              <TH>Bundle</TH>
              <TH>Мешки</TH>
              <TH>Вес</TH>
              <TH>Detector avg / p95</TH>
              <TH>Время</TH>
            </TR>
          </THead>
          <TBody>
            {runs.map(({ job, hypothesis }) => (
              <TR key={job.job_id}>
                <TD>
                  <div className="font-medium">{hypothesis || "Без названия"}</div>
                  <div className="font-mono text-[10px] text-[var(--muted-foreground)]">{job.job_id.slice(0, 8)}</div>
                </TD>
                <TD>{job.bundle_id}</TD>
                <TD>{job.summary?.total ?? "—"}</TD>
                <TD>{job.summary ? `${job.summary.total_weight_kg.toLocaleString("ru-RU")} кг` : "—"}</TD>
                <TD>
                  {job.summary
                    ? `${job.summary.detector_inference_avg_ms.toFixed(1)} / ${job.summary.detector_inference_p95_ms.toFixed(1)} мс`
                    : "—"}
                </TD>
                <TD>{job.summary ? `${job.summary.elapsed_seconds.toFixed(1)} с` : "—"}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export function ModelTestWorkbench() {
  const runner = useModelTestRunner();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [line, setLine] = useState<NormalizedLine>(() => defaultCountingLine());
  const [direction, setDirection] = useState<LineDirection>("any");
  const [inferenceFps, setInferenceFps] = useState(12);
  const [hypothesis, setHypothesis] = useState("");
  const [editingLine, setEditingLine] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<ModelTestEvent | null>(null);
  const [runs, setRuns] = useState<SavedRun[]>([]);
  const initialized = useRef(false);
  const recordedJobs = useRef(new Set<string>());
  const hypothesisByJob = useRef(new Map<string, string>());
  const pendingHypothesis = useRef("");
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!runner.info || initialized.current) return;
    initialized.current = true;
    const ready =
      runner.info.bundles.find((bundle) => bundle.id === "production" && bundle.ready) ??
      runner.info.bundles.find((bundle) => bundle.ready);
    setBundleId(ready?.id ?? "");
    setLine(normalizeCountingLine(runner.info.defaults.line) ?? defaultCountingLine());
    setDirection(normalizeLineDirection(runner.info.defaults.direction));
    setInferenceFps(runner.info.defaults.inference_fps);
  }, [runner.info]);

  useEffect(() => {
    if (!file) {
      setVideoUrl("");
      return;
    }
    const next = URL.createObjectURL(file);
    setVideoUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  useEffect(() => {
    const job = runner.job;
    if (job?.status !== "completed" || !job.summary || recordedJobs.current.has(job.job_id)) return;
    recordedJobs.current.add(job.job_id);
    const saved = { job, hypothesis: hypothesisByJob.current.get(job.job_id) ?? pendingHypothesis.current };
    setRuns((current) => [saved, ...current].slice(0, 8));
  }, [runner.job]);

  const readyBundles = runner.info?.bundles.filter((bundle) => bundle.ready) ?? [];
  const selectedBundle = runner.info?.bundles.find((bundle) => bundle.id === bundleId) ?? null;
  const maxBytes = runner.info?.limits.max_upload_bytes ?? 512 * 1024 * 1024;
  const productionBlocked = Boolean(runner.info?.reject_while_processors_active && runner.info.active_processors > 0);
  const maxFps = runner.info?.limits.max_inference_fps ?? 30;
  const startDisabled =
    runner.active ||
    !runner.info?.enabled ||
    productionBlocked ||
    !file ||
    !selectedBundle?.ready ||
    !validCountingLine(line) ||
    inferenceFps <= 0 ||
    inferenceFps > maxFps;

  const selectedDetection = useMemo<AlwaysOnDetection[] | undefined>(() => {
    if (!selectedEvent) return undefined;
    return [
      {
        bbox: selectedEvent.bbox,
        class_name: `${selectedEvent.class_name} · ${selectedEvent.brand} · ${selectedEvent.color}`,
        confidence: selectedEvent.confidence,
        counted: true,
      },
    ];
  }, [selectedEvent]);

  const submit = async () => {
    if (!file || startDisabled) return;
    const validationError = validateModelTestFile(file, maxBytes);
    if (validationError) {
      setFileError(validationError);
      return;
    }
    setSelectedEvent(null);
    setEditingLine(false);
    pendingHypothesis.current = hypothesis.trim();
    const result = await runner.start(file, {
      bundle: bundleId,
      line,
      direction,
      inferenceFps,
    });
    if (result) hypothesisByJob.current.set(result.job_id, hypothesis.trim());
  };

  const selectEvent = (event: ModelTestEvent) => {
    setSelectedEvent(event);
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    video.currentTime = event.video_time_sec;
  };

  if (!runner.info) {
    return <DataGate loading={runner.infoLoading} error={runner.infoError} onRetry={runner.reloadInfo} />;
  }

  return (
    <div className="flex flex-col gap-5">
      <div
        className={cn(
          "flex flex-wrap items-start gap-3 rounded-lg border px-4 py-3",
          productionBlocked || !runner.info.enabled
            ? "border-[var(--warning)]/30 bg-[var(--warning)]/10"
            : "border-[var(--success)]/25 bg-[var(--success)]/10",
        )}
      >
        {productionBlocked || !runner.info.enabled ? (
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-[var(--warning)]" />
        ) : (
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-[var(--success)]" />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">
            {!runner.info.enabled
              ? "Тестовый API выключен на camera-PC"
              : productionBlocked
                ? `Тест временно заблокирован: работают production processors (${runner.info.active_processors})`
                : `Изолированный тест готов · ${runner.info.device}`}
          </div>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Тест не пишет production analytics и не останавливает камеры. Модели выбираются только из серверного
            allowlist.
          </p>
        </div>
        <Badge tone="outline">{readyBundles.length} ready bundle</Badge>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,.75fr)]">
        <Card>
          <CardHeader className="flex-row items-center justify-between gap-3">
            <div>
              <CardTitle>Видео и линия подсчёта</CardTitle>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                Видео остаётся локальным в preview; в camera-PC уходит только после запуска.
              </p>
            </div>
            {file ? (
              <Button
                variant={editingLine ? "default" : "outline"}
                size="sm"
                disabled={runner.active}
                onClick={() => setEditingLine((value) => !value)}
              >
                <PencilLine className="size-4" /> {editingLine ? "Готово" : "Править линию"}
              </Button>
            ) : null}
          </CardHeader>
          <CardContent>
            {!file ? (
              <VideoDropZone
                file={file}
                maxBytes={maxBytes}
                disabled={runner.active}
                onFile={setFile}
                onReject={setFileError}
              />
            ) : (
              <div className="flex flex-col gap-3">
                <div className="relative aspect-video overflow-hidden rounded-lg bg-black">
                  <video
                    ref={videoRef}
                    src={videoUrl}
                    controls={!editingLine}
                    preload="metadata"
                    className="size-full object-contain"
                    onTimeUpdate={(event) => {
                      if (
                        selectedEvent &&
                        Math.abs(event.currentTarget.currentTime - selectedEvent.video_time_sec) > 0.75
                      ) {
                        setSelectedEvent(null);
                      }
                    }}
                  />
                  <CameraCountingLineOverlay
                    line={line}
                    direction={direction}
                    editable={editingLine}
                    disabled={runner.active}
                    onLineChange={setLine}
                  />
                  <DetectionOverlay
                    detections={selectedDetection}
                    frame={
                      runner.job?.input ? { width: runner.job.input.width, height: runner.job.input.height } : null
                    }
                  />
                </div>
                <VideoDropZone
                  file={file}
                  maxBytes={maxBytes}
                  disabled={runner.active}
                  onFile={(next) => {
                    setSelectedEvent(null);
                    setFile(next);
                  }}
                  onReject={setFileError}
                />
                {selectedEvent ? (
                  <div className="grid gap-2 rounded-lg border border-[var(--ring)]/25 bg-[var(--ring)]/5 p-3 text-xs sm:grid-cols-4">
                    <div>
                      <span className="text-[var(--muted-foreground)]">Событие</span>
                      <div className="mt-0.5 font-medium">
                        #{selectedEvent.index} · {selectedEvent.video_time_sec.toFixed(2)} с
                      </div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Frame / track</span>
                      <div className="mt-0.5 font-medium">
                        {selectedEvent.frame} / {selectedEvent.track_id}
                      </div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">BBox</span>
                      <div className="mt-0.5 font-mono">
                        {selectedEvent.bbox.map((value) => value.toFixed(1)).join(", ")}
                      </div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Точка / направление</span>
                      <div className="mt-0.5 font-mono">
                        {selectedEvent.point.map((value) => value.toFixed(1)).join(", ")} · {selectedEvent.direction}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            )}
            {fileError ? (
              <div className="mt-3">
                <ErrorAlert message={fileError} />
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Гипотеза и параметры</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div>
              <Label htmlFor="model-test-hypothesis">Название гипотезы</Label>
              <Input
                id="model-test-hypothesis"
                value={hypothesis}
                maxLength={120}
                disabled={runner.active}
                placeholder="Например: новая brand model на Gazel"
                onChange={(event) => setHypothesis(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="model-test-bundle">Bundle моделей</Label>
              <Select
                id="model-test-bundle"
                value={bundleId}
                disabled={runner.active}
                onChange={(event) => setBundleId(event.target.value)}
              >
                <option value="">Выберите bundle</option>
                {runner.info.bundles.map((bundle) => (
                  <option key={bundle.id} value={bundle.id} disabled={!bundle.ready}>
                    {bundle.id}
                    {bundle.ready ? "" : " · not ready"}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="model-test-direction">Направление</Label>
                <Select
                  id="model-test-direction"
                  value={direction}
                  disabled={runner.active}
                  onChange={(event) => setDirection(event.target.value as LineDirection)}
                >
                  <option value="any">Любое</option>
                  <option value="positive">Positive</option>
                  <option value="negative">Negative</option>
                  <option value="up">Вверх</option>
                  <option value="down">Вниз</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="model-test-fps">Inference FPS</Label>
                <Input
                  id="model-test-fps"
                  type="number"
                  min={0.1}
                  max={maxFps}
                  step={0.1}
                  value={inferenceFps}
                  disabled={runner.active}
                  onChange={(event) => setInferenceFps(Number(event.target.value))}
                />
              </div>
            </div>
            <div className="rounded-lg bg-[var(--muted)]/50 p-3 text-xs text-[var(--muted-foreground)]">
              Линия:{" "}
              <span className="font-mono text-[var(--foreground)]">
                {[line.x1, line.y1, line.x2, line.y2].map((value) => value.toFixed(3)).join(", ")}
              </span>
            </div>
            {runner.submitting ? (
              <div className="rounded-lg border p-3">
                <div className="mb-2 flex justify-between text-xs">
                  <span>Загрузка в camera-PC</span>
                  <span className="tabular-nums">{Math.round(runner.uploadProgress)}%</span>
                </div>
                <ProgressBar pct={runner.uploadProgress} className="h-2" />
              </div>
            ) : null}
            <div className="flex gap-2">
              <Button className="flex-1" disabled={startDisabled} onClick={() => void submit()}>
                <Play className="size-4" /> {runner.active ? "Тест выполняется" : "Запустить тест"}
              </Button>
              {runner.submitting ? (
                <Button variant="outline" onClick={runner.cancelUpload}>
                  <Square className="size-4" /> Остановить загрузку
                </Button>
              ) : null}
            </div>
            <p className="text-xs text-[var(--muted-foreground)]">
              После принятия job отмены нет: camera-PC завершит его или вернёт timeout/error.
            </p>
          </CardContent>
        </Card>
      </div>

      {selectedBundle ? <BundleDetails bundle={selectedBundle} /> : null}
      {runner.error ? <ErrorAlert message={runner.error} /> : null}
      <JobProgress job={runner.job} status={runner.status} />

      {runner.job?.status === "failed" ? (
        <ErrorAlert message={runner.job.error?.message ?? "Модель не смогла обработать видео"} />
      ) : null}
      {runner.job?.status === "completed" ? (
        <>
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--success)]">
            <CheckCircle2 className="size-5" /> Гипотеза рассчитана; выберите событие для проверки на видео.
          </div>
          <ModelTestResults
            job={runner.job}
            selectedEvent={selectedEvent}
            onSelectEvent={selectEvent}
            loadingMore={runner.loadingMore}
            onLoadMore={() => void runner.loadMoreEvents()}
          />
        </>
      ) : null}
      <RunComparison runs={runs} />

      <div className="flex items-start gap-2 rounded-lg border border-[var(--ring)]/20 bg-[var(--ring)]/5 px-4 py-3 text-xs text-[var(--muted-foreground)]">
        <FlaskConical className="mt-0.5 size-4 shrink-0 text-[var(--ring)]" />
        Этот экран сравнивает counts, distributions и latency на неразмеченном видео. Accuracy / precision / recall
        появятся только при добавлении ground-truth разметки.
      </div>
    </div>
  );
}
