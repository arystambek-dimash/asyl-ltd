"use client";

import { Boxes, Clock3, Gauge, ScanLine, Tags, Weight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { formatBytes, type ModelTestEvent, type ModelTestJob, type ModelTestModelInfo } from "@/lib/model-tests";

function number(value: number, digits = 1): string {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function confidence(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    classification_error: "Ошибка классификации",
    needs_review: "Нужна проверка",
    recognized: "Распознано",
    white_reverse: "Белый · без классификаторов",
  };
  return labels[value] ?? value;
}

function Distribution({ title, values }: { title: string; values: Record<string, number> }) {
  const rows = Object.entries(values).toSorted((a, b) => b[1] - a[1]);
  const maximum = rows.reduce((current, [, value]) => Math.max(current, value), 0);
  return (
    <div className="rounded-lg border p-4">
      <div className="mb-3 text-sm font-semibold">{title}</div>
      {rows.length === 0 ? (
        <div className="text-xs text-[var(--muted-foreground)]">Нет данных</div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {rows.map(([label, value]) => (
            <div key={label}>
              <div className="mb-1 flex justify-between gap-3 text-xs">
                <span className="truncate">{label}</span>
                <span className="tabular-nums text-[var(--muted-foreground)]">{value}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
                <div
                  className="h-full rounded-full bg-[var(--ring)]"
                  style={{ width: `${maximum > 0 ? (value / maximum) * 100 : 0}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RuntimeModel({ title, model }: { title: string; model: ModelTestModelInfo }) {
  return (
    <div className="rounded-lg border p-4 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-semibold">{title}</span>
        <Badge tone={model.loaded ? "success" : "destructive"}>{model.loaded ? "loaded" : "not loaded"}</Badge>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[var(--muted-foreground)]">
        <dt>ID</dt>
        <dd className="truncate text-right text-[var(--foreground)]" title={model.id}>
          {model.id}
        </dd>
        <dt>SHA-256</dt>
        <dd className="text-right font-mono text-[var(--foreground)]" title={model.sha256}>
          {model.sha256.slice(0, 12)}…
        </dd>
        <dt>Runtime</dt>
        <dd className="text-right text-[var(--foreground)]">
          {model.device} · {model.fp16 ? "FP16" : "FP32"}
        </dd>
        <dt>Input</dt>
        <dd className="text-right text-[var(--foreground)]">
          {model.input_size}px · {model.classes.length} классов
        </dd>
        <dt>Version</dt>
        <dd className="truncate text-right text-[var(--foreground)]" title={model.version}>
          {model.version}
        </dd>
        <dt>Instances</dt>
        <dd className="text-right text-[var(--foreground)]">{model.instances}</dd>
        <dt>Classes</dt>
        <dd className="truncate text-right text-[var(--foreground)]" title={model.classes.join(", ")}>
          {model.classes.join(", ")}
        </dd>
      </dl>
    </div>
  );
}

export function ModelTestResults({
  job,
  selectedEvent,
  onSelectEvent,
  loadingMore,
  onLoadMore,
}: {
  job: ModelTestJob;
  selectedEvent: ModelTestEvent | null;
  onSelectEvent: (event: ModelTestEvent) => void;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const summary = job.summary;
  if (!summary) return null;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
        <StatCard label="Пересечений" value={number(summary.total, 0)} icon={ScanLine} accent />
        <StatCard label="Вес" value={`${number(summary.total_weight_kg, 1)} кг`} icon={Weight} />
        <StatCard label="Детекций" value={number(summary.detections, 0)} icon={Boxes} />
        <StatCard
          label="Detector avg / p95"
          value={`${number(summary.detector_inference_avg_ms)} / ${number(summary.detector_inference_p95_ms)} мс`}
          icon={Gauge}
        />
        <StatCard label="Время расчёта" value={`${number(summary.elapsed_seconds)} с`} icon={Clock3} />
        <StatCard label="Classification avg" value={`${number(summary.classification_avg_ms)} мс`} icon={Tags} />
        <StatCard
          label="Ошибки классификации"
          value={number(summary.classification_errors, 0)}
          tone={summary.classification_errors > 0 ? "destructive" : "success"}
          caption={`${number(summary.processed_frames, 0)} processed / ${number(summary.decoded_frames, 0)} decoded`}
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Distribution title="Классы detector" values={summary.per_detector_class} />
        <Distribution title="Наблюдения detector" values={summary.detector_observations_by_class} />
        <Distribution title="Цвет" values={summary.per_classified_color} />
        <Distribution title="Бренд" values={summary.per_brand} />
        <Distribution title="SKU" values={summary.per_sku} />
        <Distribution title="Статус классификации" values={summary.per_classification_status} />
      </div>

      {job.input ? (
        <Card>
          <CardHeader>
            <CardTitle>Проверенный входной файл</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-[var(--muted-foreground)]">SHA-256</dt>
                <dd className="mt-1 break-all font-mono">{job.input.sha256}</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Видео</dt>
                <dd className="mt-1">
                  {job.input.width}×{job.input.height} ·{" "}
                  {job.input.fps === null ? "FPS неизвестен" : `${number(job.input.fps, 2)} FPS`}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Длительность / кадры</dt>
                <dd className="mt-1">
                  {job.input.duration_seconds === null ? "—" : `${number(job.input.duration_seconds, 2)} с`} ·{" "}
                  {number(job.input.frame_count, 0)}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Размер / MIME</dt>
                <dd className="mt-1">
                  {formatBytes(job.input.size_bytes)} · {job.input.content_type}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ) : null}

      {job.bundle ? (
        <Card>
          <CardHeader>
            <CardTitle>Фактически загруженные модели</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 lg:grid-cols-3">
            <RuntimeModel title="Detector" model={job.bundle.detector} />
            <RuntimeModel title="Color classifier" model={job.bundle.classifiers.color_model} />
            <RuntimeModel title="Brand classifier" model={job.bundle.classifiers.brand_model} />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <div>
            <CardTitle>Crossing events</CardTitle>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              Нажмите событие, чтобы перейти к таймкоду и показать bbox на исходном видео.
            </p>
          </div>
          <Badge tone="outline">{job.page.total_events} событий</Badge>
        </CardHeader>
        <CardContent>
          {job.events.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">Пересечений не найдено.</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH># / время</TH>
                  <TH>Frame / track</TH>
                  <TH>Detector</TH>
                  <TH>Цвет</TH>
                  <TH>Бренд / SKU</TH>
                  <TH>Статус</TH>
                </TR>
              </THead>
              <TBody>
                {job.events.map((event) => (
                  <TR
                    key={event.index}
                    className={selectedEvent?.index === event.index ? "bg-[var(--ring)]/10" : undefined}
                  >
                    <TD>
                      <Button variant="link" className="h-auto p-0 text-left" onClick={() => onSelectEvent(event)}>
                        #{event.index} · {number(event.video_time_sec, 2)} с
                      </Button>
                    </TD>
                    <TD>
                      <div>{event.frame}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        track {event.track_id} · {event.direction}
                      </div>
                    </TD>
                    <TD>
                      <div className="font-medium">{event.class_name}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {confidence(event.confidence)} · {number(event.weight_kg)} кг
                      </div>
                    </TD>
                    <TD>
                      {event.color}{" "}
                      <span className="text-xs text-[var(--muted-foreground)]">
                        {confidence(event.color_confidence)}
                      </span>
                    </TD>
                    <TD>
                      <div>
                        {event.brand}{" "}
                        <span className="text-xs text-[var(--muted-foreground)]">
                          {confidence(event.brand_confidence)}
                        </span>
                      </div>
                      <div className="text-xs text-[var(--muted-foreground)]">{event.sku || "—"}</div>
                    </TD>
                    <TD>
                      <Badge tone={event.classification_status === "recognized" ? "success" : "warning"}>
                        {statusLabel(event.classification_status)}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          {job.page.has_more ? (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" disabled={loadingMore} onClick={onLoadMore}>
                {loadingMore ? "Загрузка…" : "Показать ещё события"}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
